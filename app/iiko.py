from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

import httpx


IIKO_BASE_URL = "https://api-ru.iiko.services"


class IikoError(RuntimeError):
    pass


@dataclass
class IikoDiagnostic:
    organizations: list[dict[str, Any]] = field(default_factory=list)
    terminal_groups: int = 0
    external_menus: list[dict[str, Any]] = field(default_factory=list)
    stop_list_groups: int = 0
    orders: list[dict[str, Any]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class IikoOrderItem:
    name: str
    amount: float
    modifiers: tuple[str, ...] = ()

    def spoken(self) -> str:
        quantity = int(self.amount) if self.amount.is_integer() else self.amount
        value = self.name if self.amount == 1 else f"{quantity} порции {self.name}"
        if self.modifiers:
            value += f" с добавками: {', '.join(self.modifiers)}"
        return value


@dataclass(frozen=True)
class IikoOrderSnapshot:
    organization: str
    number: str
    status: str
    confirmed_at: datetime
    items: tuple[IikoOrderItem, ...]
    total: float | None
    address: str

    def spoken_items(self) -> str:
        return ", ".join(item.spoken() for item in self.items) or "состав заказа не указан"

    def spoken_total(self) -> str:
        if self.total is None:
            return ""
        total = int(self.total) if self.total.is_integer() else self.total
        return f"Сумма заказа {total} рублей."

    def greeting(self) -> str:
        delivery = f"Адрес доставки: {self.address}." if self.address else "Адрес доставки в iiko не указан."
        return (
            f"Здравствуйте! Это Sushi House. Звоню для подтверждения заказа номер {self.number}. "
            f"В заказе: {self.spoken_items()}. {self.spoken_total()} {delivery} "
            "Подтверждаете заказ и адрес доставки?"
        )

    def prompt_context(self) -> str:
        return (
            "Данные тестового заказа, которые нельзя изменять:\n"
            f"Ресторан: {self.organization}.\n"
            f"Номер: {self.number}.\n"
            f"Состав: {self.spoken_items()}.\n"
            f"Сумма: {self.total if self.total is not None else 'не указана'}.\n"
            f"Адрес: {self.address or 'не указан'}.\n"
            "Попроси подтвердить состав и адрес. Если клиент подтверждает, скажи, что подтверждение "
            "зафиксировано только в тестовом журнале и не отправлено в iiko."
        )


class IikoClient:
    def __init__(
        self,
        api_login: str,
        app_id: str,
        client_secret: str,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self.credentials = {
            "apiLogin": api_login,
            "appId": app_id,
            "clientSecret": client_secret,
        }
        self.client = httpx.AsyncClient(
            base_url=IIKO_BASE_URL,
            timeout=httpx.Timeout(30),
            transport=transport,
            headers={"Content-Type": "application/json"},
        )
        self.token: str | None = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        await self.client.aclose()

    async def post(self, path: str, payload: dict[str, Any], *, authenticated: bool = True) -> dict[str, Any]:
        headers = {}
        if authenticated:
            if not self.token:
                await self.authenticate()
            headers["Authorization"] = f"Bearer {self.token}"
        try:
            response = await self.client.post(path, json=payload, headers=headers)
            response.raise_for_status()
            data = response.json()
        except httpx.HTTPStatusError as exc:
            request_id = exc.response.headers.get("x-correlation-id", "нет")
            raise IikoError(f"iiko вернул HTTP {exc.response.status_code}; correlation ID: {request_id}") from exc
        except (httpx.HTTPError, ValueError) as exc:
            raise IikoError("Не удалось получить корректный ответ iikoCloud") from exc
        if not isinstance(data, dict):
            raise IikoError("iikoCloud вернул ответ неожиданного формата")
        return data

    async def authenticate(self) -> None:
        data = await self.post("/api/v2/access_token", self.credentials, authenticated=False)
        self.token = data.get("token")
        if not self.token:
            raise IikoError("Авторизация выполнена, но токен в ответе отсутствует")

    async def latest_accepted_starter_order(self, lookback_days: int = 2) -> IikoOrderSnapshot | None:
        organizations = (await self.post("/api/1/organizations", {})).get("organizations", [])
        candidates: list[IikoOrderSnapshot] = []
        today = date.today()
        for organization in organizations:
            organization_id = organization.get("id")
            if not organization_id:
                continue
            data = await self.post(
                "/api/1/deliveries/by_delivery_date_and_status",
                {
                    "organizationIds": [organization_id],
                    "deliveryDateFrom": f"{(today - timedelta(days=lookback_days)).isoformat()} 00:00:00.000",
                    "deliveryDateTo": f"{(today + timedelta(days=1)).isoformat()} 00:00:00.000",
                },
            )
            for group in data.get("ordersByOrganizations", []):
                for wrapper in group.get("orders", []):
                    order = wrapper.get("order") or {}
                    if order.get("sourceKey") != "Starter":
                        continue
                    status = str(order.get("status") or wrapper.get("status") or "")
                    if status == "Cancelled" or order.get("cancelInfo") or not order.get("whenConfirmed"):
                        continue
                    confirmed_at = self._parse_iiko_datetime(str(order["whenConfirmed"]))
                    if not confirmed_at:
                        continue
                    candidates.append(IikoOrderSnapshot(
                        organization=str(organization.get("name") or organization_id),
                        number=str(order.get("number") or "без номера"),
                        status=status,
                        confirmed_at=confirmed_at,
                        items=self._parse_order_items(order),
                        total=float(order["sum"]) if order.get("sum") is not None else None,
                        address=self._format_delivery_address(order.get("deliveryPoint") or {}),
                    ))
        return max(candidates, key=lambda item: item.confirmed_at) if candidates else None

    @staticmethod
    def _parse_iiko_datetime(value: str) -> datetime | None:
        try:
            return datetime.fromisoformat(value.replace(" ", "T", 1))
        except ValueError:
            return None

    @staticmethod
    def _parse_order_items(order: dict[str, Any]) -> tuple[IikoOrderItem, ...]:
        raw_items = list(order.get("items") or [])
        for combo in order.get("combos") or []:
            raw_items.extend(combo.get("items") or [])
        items = []
        for item in raw_items:
            product = item.get("product") or {}
            name = str(product.get("name") or item.get("name") or "Позиция без названия")
            try:
                amount = float(item.get("amount") or 1)
            except (TypeError, ValueError):
                amount = 1.0
            modifiers = []
            for modifier in item.get("modifiers") or []:
                modifier_product = modifier.get("product") or {}
                modifier_name = modifier_product.get("name") or modifier.get("name")
                if modifier_name:
                    modifiers.append(str(modifier_name))
            items.append(IikoOrderItem(name=name, amount=amount, modifiers=tuple(modifiers)))
        return tuple(items)

    @staticmethod
    def _format_delivery_address(delivery_point: dict[str, Any]) -> str:
        address = delivery_point.get("address") or {}
        if not address:
            return ""
        parts = []
        line = address.get("line1")
        if line:
            parts.append(str(line))
        else:
            street = address.get("street") or {}
            city = (street.get("city") or {}).get("name")
            if city:
                parts.append(str(city))
            if street.get("name"):
                parts.append(str(street["name"]))
            if address.get("house"):
                parts.append(f"дом {address['house']}")
        for field_name, label in (("building", "корпус"), ("flat", "квартира"), ("entrance", "подъезд"), ("floor", "этаж"), ("doorphone", "домофон")):
            if address.get(field_name):
                parts.append(f"{label} {address[field_name]}")
        return ", ".join(parts)

    async def diagnose(self, date_from: date, date_to: date) -> IikoDiagnostic:
        result = IikoDiagnostic()
        org_data = await self.post("/api/1/organizations", {})
        result.organizations = org_data.get("organizations", [])
        organization_ids = [item.get("id") for item in result.organizations if item.get("id")]
        if not organization_ids:
            result.errors.append("Доступных организаций не найдено.")
            return result

        await self._diagnose_terminals(result, organization_ids)
        await self._diagnose_menu(result, organization_ids)
        await self._diagnose_stop_lists(result, organization_ids)
        await self._diagnose_orders(result, organization_ids, date_from, date_to)
        return result

    async def _diagnose_terminals(self, result: IikoDiagnostic, organization_ids: list[str]) -> None:
        try:
            data = await self.post("/api/1/terminal_groups", {"organizationIds": organization_ids})
            result.terminal_groups = sum(
                len(group.get("items", [])) for group in data.get("terminalGroups", [])
            )
        except IikoError as exc:
            result.errors.append(f"Терминалы: {exc}")

    async def _diagnose_menu(self, result: IikoDiagnostic, organization_ids: list[str]) -> None:
        try:
            data = await self.post("/api/2/menu", {"organizationIds": organization_ids})
            result.external_menus = data.get("externalMenus", [])
        except IikoError as exc:
            result.errors.append(f"Меню: {exc}")

    async def _diagnose_stop_lists(self, result: IikoDiagnostic, organization_ids: list[str]) -> None:
        try:
            data = await self.post("/api/1/stop_lists", {"organizationIds": organization_ids})
            result.stop_list_groups = len(data.get("terminalGroupStopLists", []))
        except IikoError as exc:
            result.errors.append(f"Стоп-листы: {exc}")

    async def _diagnose_orders(
        self,
        result: IikoDiagnostic,
        organization_ids: list[str],
        date_from: date,
        date_to: date,
    ) -> None:
        organization_names = {item.get("id"): item.get("name", "Без названия") for item in result.organizations}
        for organization_id in organization_ids:
            try:
                data = await self.post(
                    "/api/1/deliveries/by_delivery_date_and_status",
                    {
                        "organizationIds": [organization_id],
                        "deliveryDateFrom": f"{date_from.isoformat()} 00:00:00.000",
                        "deliveryDateTo": f"{(date_to + timedelta(days=1)).isoformat()} 00:00:00.000",
                    },
                )
                for group in data.get("ordersByOrganizations", []):
                    for wrapper in group.get("orders", []):
                        order = wrapper.get("order") or {}
                        courier_info = order.get("courierInfo") or {}
                        courier = courier_info.get("courier") or {}
                        result.orders.append(
                            {
                                "organization": organization_names.get(organization_id, organization_id),
                                "number": order.get("number") or "—",
                                "status": wrapper.get("status") or order.get("status") or "—",
                                "source": order.get("sourceKey") or "—",
                                "customer": (order.get("customer") or {}).get("name") or "—",
                                "phone": order.get("phone") or (order.get("customer") or {}).get("phone") or "—",
                                "courier": courier.get("name") or courier.get("displayName") or "Не назначен",
                                "sum": order.get("sum"),
                            }
                        )
                for error in data.get("errors", []):
                    result.errors.append(f"Заказы · {organization_names.get(organization_id)}: {error}")
            except IikoError as exc:
                result.errors.append(f"Заказы · {organization_names.get(organization_id)}: {exc}")
