# Coin product setup

Catalog: 5 coins / KRW 4,000; 10 / 8,000; 20 / 16,000; 50 / 40,000 (+5 bonus).

1. Run `python manage.py migrate` in the target environment.
2. Add a sandbox API key with price read/write permissions to `.env` as `PADDLE_API_KEY`.
3. Use an existing sandbox Paddle product ID to run:
   `python manage.py create_sandbox_20_price --product-id pro_REPLACE`
   This uses only sandbox-api.paddle.com, reuses a matching price on repeat runs, and prints its price ID.
4. Confirm the 5/10/50 sandbox prices in Paddle, then run:
   `python manage.py configure_coin_products --price-5 pri_REPLACE5 --price-10 pri_REPLACE10 --price-20 pri_REPLACE20 --price-50 pri_REPLACE50`
   All four IDs must belong to the same Paddle environment as the frontend checkout.
   This command disables other products (including 1 coin) without deleting order history.
   Existing product IDs are preserved. Conflicting price references require manual review.

GET /api/payments/products/ returns base `coin_amount` and separate `bonus_coin_amount` (0 or 5).
The frontend can show the 50-coin product with +5 bonus. Order `coin_amount` is the total to grant (55).
The total is snapshotted when the order is created, so later catalog edits cannot alter the grant.
Full refunds revoke the credited total, limited to the remaining wallet balance as before.
Existing orders retain their original credited amount; no retroactive bonus is applied.

The schema migration alone does not configure products. Run the catalog command after confirming real price IDs.
Do not mix sandbox prices with production checkout. Production Paddle prices must be prepared separately.
Paddle reference: https://developer.paddle.com/api-reference/prices/create-price/
