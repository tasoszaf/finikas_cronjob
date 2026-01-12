#!/usr/bin/env python
# -*- coding: utf-8 -*-

import requests
import pandas as pd
from datetime import datetime, timedelta
import os

# =====================================================
# CONFIG
# =====================================================

API_URL_AVAIL = "https://login.smoobu.com/booking/checkApartmentAvailability"
API_URL_RATES = "https://login.smoobu.com/api/rates"

API_KEY = os.getenv("SMOOBU_API_KEY")
CUSTOMER_ID = int(os.getenv("SMOOBU_CUSTOMER_ID"))

APARTMENTS = [
    2715198, 2715203, 2715218, 2715223, 2715238,
    2715273, 2715193, 2715208, 2715213, 2715228, 2715233
]

TEST_MODE = False   
EXCEL_PATH = "data_finikas.xlsx"

LONG_TERM_LIMIT = 240
LONG_TERM_PREMIUMS = {
    2715198: 15,
    2715203: 15,
    2715218: 15,
    2715223: 15,
    2715238: 15,
    2715273: 15,
    2715193: 20,
    2715208: 20,
    2715213: 20,
    2715228: 20,
    2715233: 30
}

# =====================================================
# LOAD EXCEL
# =====================================================

df = pd.read_excel(EXCEL_PATH)
df["date"] = pd.to_datetime(df["date"]).dt.date

headers = {
    "Api-Key": API_KEY,
    "Content-Type": "application/json"
}

TOTAL_ROOMS = len(APARTMENTS)

# =====================================================
# FUNCTIONS
# =====================================================

def get_total_occupancy(date_str):
    arrival = date_str
    departure = (
        datetime.strptime(date_str, "%Y-%m-%d") + timedelta(days=1)
    ).strftime("%Y-%m-%d")

    payload = {
        "arrivalDate": arrival,
        "departureDate": departure,
        "apartments": APARTMENTS,
        "customerId": CUSTOMER_ID
    }

    r = requests.post(API_URL_AVAIL, headers=headers, json=payload)
    r.raise_for_status()

    data = r.json()
    available = data.get("availableApartments", [])

    occupied = TOTAL_ROOMS - len(available)
    occ = occupied / TOTAL_ROOMS if TOTAL_ROOMS else 0

    return occ, available


def calculate_price(current_occ, target_date, today, apartment_id=None):
    difference = (target_date - today).days

    if difference < 0 or difference > 365:
        return None, None, None, None

    row = df.loc[df["date"] == target_date]
    if row.empty:
        return None, None, None, None

    min_price = float(row["min_price"].iloc[0])
    target_price = float(row["target_price"].iloc[0])
    max_price = float(row["max_price"].iloc[0])

    # -------- SPECIAL CASE: TODAY --------
    if difference == 0:
        return min_price, None, min_price, max_price

    # -------- LONG TERM --------
    if difference > LONG_TERM_LIMIT:
        premium = LONG_TERM_PREMIUMS.get(apartment_id, 20)  # default 20
        price = min(max_price, target_price + premium)
        return round(price, 2), None, min_price, max_price

    # -------- SCORE BASED --------
    if current_occ == 0:
        pace_ratio = (difference - LONG_TERM_LIMIT) / difference
        x = pace_ratio
        occupancy_ratio = None
    else:
        temp = df.copy()
        temp["diff_occ"] = abs(temp["sum_occupancy_days_ahead"] - current_occ)
        closest = temp.loc[temp["diff_occ"].idxmin()]

        plan_day = int(closest["days_diff"])
        pace_ratio = (difference - plan_day) / difference

        plan_row = df.loc[df["days_diff"] == difference]
        plan_occ = (
            float(plan_row["sum_occupancy_days_ahead"].iloc[0])
            if not plan_row.empty else current_occ
        )

        denom = min(current_occ, plan_occ)
        occupancy_ratio = max(current_occ, plan_occ) / denom if denom > 0 else 1
        occupancy_ratio = min(occupancy_ratio, 2)

        x = pace_ratio * occupancy_ratio

    # -------- FINAL PRICE --------
    if x >= 0:
        price = x * (max_price - target_price) + target_price
    else:
        price = x * (target_price - min_price) + target_price

    price = max(min_price, min(price, max_price))
    return round(price, 2), round(x, 4), min_price, max_price


def send_price(apartment_id, date_str, price):
    if TEST_MODE:
        print(f"        🏠 Apt {apartment_id} → {price} €")
        return

    payload = {
        "apartments": [apartment_id],
        "operations": [{
            "dates": [date_str],
            "daily_price": price,
            "min_length_of_stay": 1
        }]
    }

    r = requests.post(API_URL_RATES, headers=headers, json=payload)
    r.raise_for_status()

# =====================================================
# MAIN LOOP (PRINT EACH APARTMENT PRICE, UNIQUE ONLY)
# =====================================================

results = []

start = datetime.now().date()
end   = datetime(2026, 13, 1).date()

current = start

while current <= end:
    date_str = current.strftime("%Y-%m-%d")

    try:
        occ, available = get_total_occupancy(date_str)
    except Exception as e:
        print(f"⚠️ {date_str} | Availability error: {e}")
        current += timedelta(days=1)
        continue

    if not available:
        print(f"\n📅 {date_str} | ❌ No availability")
        current += timedelta(days=1)
        continue

    # ✅ Αφαιρούμε όλα τα διπλά και κρατάμε μόνο μοναδικά
    seen = set()
    unique_available = []
    for apt in available:
        if apt not in seen:
            seen.add(apt)
            unique_available.append(apt)

    # Διαβάζουμε τιμές από Excel
    row = df.loc[df["date"] == current]
    if row.empty:
        print(f"⚠️ {date_str} | No pricing data in Excel")
        current += timedelta(days=1)
        continue

    min_price = float(row["min_price"].iloc[0])
    target_price = float(row["target_price"].iloc[0])
    max_price = float(row["max_price"].iloc[0])

    difference = (current - datetime.now().date()).days

    # -------- SPECIAL CASE: TODAY --------
    if difference == 0:
        print(f"\n📅 {date_str} | Today → all apartments get min_price={min_price}€")
        for apt in unique_available:
            p = min_price
            print(f"🏠 Apt {apt} → {p} €")  
            send_price(apt, date_str, p)
            results.append({
                "date": date_str,
                "apartment_id": apt,
                "price": p,
                "occupancy": occ,
                "base_price": min_price,
                "x": None
            })
        current += timedelta(days=1)
        continue

    # -------- FUTURE DATES --------
    price, x, min_p, max_p = calculate_price(
        current_occ=occ,
        target_date=current,
        today=datetime.now().date(),
        apartment_id=unique_available[0]
    )

    if price is None:
        print(f"⚠️ {date_str} | Pricing calculation failed")
        current += timedelta(days=1)
        continue

    # Υπολογισμός step με βάση τα μοναδικά καταλύματα
    if max_p == price:
        step = 0
    else:
        step = (max_p - price) / len(unique_available)

    print(f"\n📅 {date_str} | Occ={occ:.2f} | x={x} | Base={price}")

    for i, apt in enumerate(unique_available):
        p = round(min(price + i * step, max_p), 1)
        print(f"🏠 Apt {apt} → {p} €")  
        send_price(apt, date_str, p)
        results.append({
            "date": date_str,
            "apartment_id": apt,
            "price": p,
            "occupancy": occ,
            "base_price": price,
            "x": x
        })

    current += timedelta(days=1)


print("\nFinished processing all valid dates of 2026.")
