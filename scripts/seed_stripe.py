"""
Creates additional test charges in your Stripe TEST account so the
overview/RFM views have more customers and varied amounts to work with.

Note: Stripe does not let you backdate a charge's `created` timestamp via
the API, so everything created here is dated "now" — this only enriches
customer/amount variety, not the time-based views (cohorts, trends).

Usage:
    .venv/bin/python scripts/seed_stripe.py
"""
import os
import random
import sys

import stripe
from dotenv import load_dotenv

load_dotenv()
stripe.api_key = os.getenv("STRIPE_SECRET_KEY")

if not stripe.api_key:
    sys.exit("STRIPE_SECRET_KEY not set in .env")
if not stripe.api_key.startswith("sk_test_"):
    sys.exit("Refusing to run against a non-test key. Use a sk_test_... key.")

FIRST_NAMES = ["Alice", "Bruno", "Chloé", "David", "Emma", "Farid", "Giulia", "Hugo"]
LAST_NAMES = ["Martin", "Bernard", "Dubois", "Petit", "Robert", "Moreau"]

random.seed(42)


def random_name():
    return f"{random.choice(FIRST_NAMES)} {random.choice(LAST_NAMES)}"


def main(num_customers=10, max_charges_per_customer=3):
    created = 0
    for _ in range(num_customers):
        name = random_name()
        email = name.lower().replace(" ", ".") + "@example.com"

        customer = stripe.Customer.create(name=name, email=email, source="tok_visa")

        for _ in range(random.randint(1, max_charges_per_customer)):
            amount = random.choice([900, 1500, 2900, 4900, 7900, 12000])
            stripe.Charge.create(
                amount=amount,
                currency="eur",
                customer=customer.id,
                description="Demo seed charge",
            )
            created += 1

    print(f"Created {num_customers} customers and {created} charges in your Stripe test account.")


if __name__ == "__main__":
    main()
