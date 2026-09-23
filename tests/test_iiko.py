import json
import unittest
from datetime import date

import httpx

from app.iiko import IikoClient


class IikoClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_latest_accepted_starter_order_contains_items_and_address(self):
        async def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/api/v2/access_token":
                return httpx.Response(200, json={"token": "test-token"})
            if request.url.path == "/api/1/organizations":
                return httpx.Response(200, json={"organizations": [{"id": "org-1", "name": "Смолина"}]})
            return httpx.Response(200, json={
                "ordersByOrganizations": [{
                    "organizationId": "org-1",
                    "orders": [
                        {"order": {
                            "number": "100",
                            "sourceKey": "Starter",
                            "status": "Cancelled",
                            "whenConfirmed": "2026-09-23 12:20:00.000",
                            "cancelInfo": {"reason": "test"},
                        }},
                        {"order": {
                            "number": "101",
                            "sourceKey": "Starter",
                            "status": "Closed",
                            "whenConfirmed": "2026-09-23 12:21:00.000",
                            "sum": 1630,
                            "items": [{
                                "amount": 2,
                                "product": {"name": "Филадельфия"},
                                "modifiers": [{"product": {"name": "Соевый соус"}}],
                            }],
                            "deliveryPoint": {"address": {
                                "line1": "Улан-Удэ, Балтахинова, 36",
                                "flat": "62",
                                "entrance": "3",
                                "floor": "5",
                                "doorphone": "62",
                            }},
                        }},
                    ],
                }],
            })

        async with IikoClient("login", "app", "secret", transport=httpx.MockTransport(handler)) as client:
            order = await client.latest_accepted_starter_order()

        self.assertIsNotNone(order)
        self.assertEqual(order.number, "101")
        self.assertEqual(order.items[0].name, "Филадельфия")
        self.assertIn("2 порции Филадельфия", order.greeting())
        self.assertIn("квартира 62", order.greeting())
        self.assertIn("1630 рублей", order.greeting())

    async def test_diagnostic_collects_read_only_data(self):
        async def handler(request: httpx.Request) -> httpx.Response:
            payloads = {
                "/api/v2/access_token": {"token": "test-token"},
                "/api/1/organizations": {"organizations": [{"id": "org-1", "name": "Sushi House"}]},
                "/api/1/terminal_groups": {"terminalGroups": [{"items": [{"id": "terminal-1"}]}]},
                "/api/2/menu": {"externalMenus": [{"id": "menu-1", "name": "Основное"}]},
                "/api/1/stop_lists": {"terminalGroupStopLists": [{"organizationId": "org-1"}]},
                "/api/1/deliveries/by_delivery_date_and_status": {
                    "ordersByOrganizations": [{
                        "organizationId": "org-1",
                        "orders": [{
                            "status": "OnWay",
                            "order": {
                                "number": "141737",
                                "sourceKey": "Starter",
                                "customer": {"name": "Тест"},
                                "courierInfo": {
                                    "courier": {
                                        "id": "courier-1",
                                        "name": "Курьер",
                                        "phone": "+70000000000",
                                    },
                                    "isCourierSelectedManually": False,
                                },
                                "sum": 1250,
                            },
                        }],
                    }],
                },
            }
            self.assertEqual(request.method, "POST")
            if request.url.path != "/api/v2/access_token":
                self.assertEqual(request.headers["Authorization"], "Bearer test-token")
            return httpx.Response(200, json=payloads[request.url.path])

        async with IikoClient("login", "app", "secret", transport=httpx.MockTransport(handler)) as client:
            result = await client.diagnose(date(2026, 9, 10), date(2026, 9, 11))

        self.assertEqual(len(result.organizations), 1)
        self.assertEqual(result.terminal_groups, 1)
        self.assertEqual(len(result.external_menus), 1)
        self.assertEqual(result.stop_list_groups, 1)
        self.assertEqual(len(result.orders), 1)
        self.assertEqual(result.orders[0]["courier"], "Курьер")
        self.assertEqual(result.errors, [])

    async def test_order_request_uses_inclusive_ui_date_range(self):
        captured = {}

        async def handler(request: httpx.Request) -> httpx.Response:
            path = request.url.path
            if path == "/api/v2/access_token":
                return httpx.Response(200, json={"token": "token"})
            if path == "/api/1/organizations":
                return httpx.Response(200, json={"organizations": [{"id": "org-1", "name": "One"}]})
            if path.endswith("by_delivery_date_and_status"):
                captured.update(json.loads(request.content))
                return httpx.Response(200, json={"ordersByOrganizations": []})
            return httpx.Response(200, json={})

        async with IikoClient("login", "app", "secret", transport=httpx.MockTransport(handler)) as client:
            await client.diagnose(date(2026, 9, 10), date(2026, 9, 10))

        self.assertEqual(captured["deliveryDateFrom"], "2026-09-10 00:00:00.000")
        self.assertEqual(captured["deliveryDateTo"], "2026-09-11 00:00:00.000")


if __name__ == "__main__":
    unittest.main()
