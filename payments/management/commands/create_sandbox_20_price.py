import json
import os
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from django.core.management.base import BaseCommand, CommandError


BASE_URL = "https://sandbox-api.paddle.com"


class Command(BaseCommand):
    help = "Create or reuse a sandbox one-time KRW 16,000 price for 20 coins."

    def add_arguments(self, parser):
        parser.add_argument("--product-id", required=True, help="Existing sandbox Paddle product ID (pro_...).")

    def handle(self, *args, **options):
        key = os.environ.get("PADDLE_API_KEY", "")
        if not key:
            raise CommandError("Set PADDLE_API_KEY in .env; never pass it on the command line.")
        product_id = options["product_id"]
        if not product_id.startswith("pro_"):
            raise CommandError("Provide a sandbox product ID (pro_...).")

        def request(path, data=None):
            req = Request(BASE_URL + path, data=json.dumps(data).encode() if data is not None else None,
                          headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
            try:
                with urlopen(req, timeout=30) as response:
                    return json.load(response)
            except HTTPError as exc:
                raise CommandError(f"Paddle returned HTTP {exc.code}; check sandbox API permissions and product ID.") from None
            except (URLError, TimeoutError):
                raise CommandError("Paddle connection failed. Rerun to check whether the price was created.") from None

        path = "/prices?" + urlencode({"product_id": product_id, "status": "active", "per_page": 200})
        matches = []
        while path:
            result = request(path)
            for price in result["data"]:
                if (price.get("unit_price") == {"amount": "16000", "currency_code": "KRW"}
                        and price.get("billing_cycle") is None and price.get("trial_period") is None
                        and price.get("tax_mode") == "internal"
                        and price.get("quantity") == {"minimum": 1, "maximum": 1}):
                    matches.append(price["id"])
            pagination = result.get("meta", {}).get("pagination", {})
            next_url = pagination.get("next") if pagination.get("has_more") else None
            if next_url and not next_url.startswith(BASE_URL + "/"):
                raise CommandError("Unexpected Paddle pagination URL.")
            path = next_url[len(BASE_URL):] if next_url else None
        if len(matches) > 1:
            raise CommandError("Multiple matching prices exist; select the correct price in Paddle dashboard.")
        if matches:
            price_id = matches[0]
        else:
            result = request("/prices", {
                "product_id": product_id, "description": "20 coins - KRW 16,000",
                "name": "20\uD1A8", "unit_price": {"amount": "16000", "currency_code": "KRW"},
                "billing_cycle": None, "tax_mode": "internal",
                "quantity": {"minimum": 1, "maximum": 1},
            })
            price_id = result["data"]["id"]
        self.stdout.write(self.style.SUCCESS(f"Sandbox 20-coin price: {price_id}"))
