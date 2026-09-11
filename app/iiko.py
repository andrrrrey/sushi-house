from dataclasses import dataclass, field
from datetime import date, timedelta
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
                        courier = order.get("courierInfo") or {}
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
