#!/usr/bin/env python
# -*- coding: utf-8 -*-

import requests
import pandas as pd
from datetime import datetime, timedelta
import os
import time

# -----------------------------
# CONFIG
# -----------------------------
API_URL_AVAIL = "https://login.smoobu.com/booking/checkApartmentAvailability"
API_URL_RATES = "https://login.smoobu.com/api/rates"

CUSTOMER_ID = int(os.getenv("SMOOBU_CUSTOMER_ID"))
API_KEY = os.getenv("SMOOBU_API_KEY")

APARTMENTS = [
    2715198, 2715203, 2715218, 2715223, 2715238,
    2715273, 2715193, 2715208, 2715213, 2715228, 2715233
]

MIN_PRICE_SAME_DAY_BY_MONTH = {
    1: 50, 2: 50, 3: 55, 4: 60,
    5: 70, 6: 80, 7: 80, 8: 80,
    9: 80, 10: 70, 11: 50, 12: 50
}

TOTAL_ROOMS = len(APARTMENTS)
TEST_MODE = False  # True για δοκιμή, False για αποστολή σε Smoobu

# Load Excel
df = pd.read_excel("data_finikas.xlsx")
df['date'] = pd.to_datetime(df['date']).dt.date  # ασφαλής σύγκριση

headers = {
    "Api-Key": API_KEY,
    "Content-Type": "application/json"
}

# -----------------------------
# FUNCTIONS
# -----------------------------
def get_total_occupancy_with_retry(date_str, apartment_ids, retries=3, timeout_sec=10):
    """Παίρνει την πληρότητα με retry + timeout"""
    for attempt in range(1, retries+1):
        try:
            arrival = date_str
            departure = (datetime.strptime(date_str, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
            payload = {
                "arrivalDate": arrival,
                "departureDate": departure,
                "apartments": apartment_ids,
                "customerId": CUSTOMER_ID
            }

            response = requests.post(API_URL_AVAIL, json=payload, headers=headers, timeout=timeout_sec)
            response.raise_for_status()
            data = response.json()

            available_apts = data.get("availableApartments", [])
            occupied_count = len(apartment_ids) - len(available_apts)
            total_occ = occupied_count / len(apartment_ids) if apartment_ids else 0
            return total_occ, available_apts

        except requests.exceptions.RequestException as e:
            print(f"⚠ Attempt {attempt} failed for {date_str}: {e}")
            time.sleep(2)

    print(f"❌ Could not get occupancy for {date_str} after {retries} attempts")
    return None, []

def calculate_price(current_occ, target_date, current_datetime):
    """Υπολογίζει τελική τιμή με composite score και hour-based same-day pricing"""
    difference = (target_date - current_datetime.date()).days

    if difference < 0 or difference > 365:
        return None, None, None, None

    row_price = df.loc[df['date'] == target_date]
    if row_price.empty:
        return None, None, None, None

    target_price = float(row_price['target_price'].iloc[0])
    max_price = float(row_price['max_price'].iloc[0])
    min_price = MIN_PRICE_SAME_DAY_BY_MONTH[target_date.month] if difference == 0 else float(row_price['min_price'].iloc[0])

    if difference > 240:
        final_price = target_price + 20
        return round(final_price, 2), None, None, None

    if difference == 0:
        hours_left = 23 - current_datetime.hour
        if current_occ == 0:
            pace_ratio = (hours_left - 263) / hours_left
            x = pace_ratio
        else:
            temp_df = df.copy()
            temp_df['diff_occ'] = abs(temp_df['sum_occupancy_days_ahead'] - current_occ)
            closest_row = temp_df.loc[temp_df['diff_occ'].idxmin()]
            closest_hour = int(closest_row['hours_diff'])
            pace_ratio = (hours_left - closest_hour) / hours_left
            plan_occ = float(df.loc[df['hours_diff'] == hours_left]['sum_occupancy_days_ahead'].values[0])
            denom = min(current_occ, plan_occ) if plan_occ != 0 else 1
            x = pace_ratio * (max(current_occ, plan_occ) / denom)
    else:
        if current_occ == 0:
            pace_ratio = (difference - 240) / difference
            x = pace_ratio
        else:
            temp_df = df.copy()
            temp_df['diff_occ'] = abs(temp_df['sum_occupancy_days_ahead'] - current_occ)
            closest_row = temp_df.loc[temp_df['diff_occ'].idxmin()]
            closest_day = int(closest_row['days_diff'])
            pace_ratio = (difference - closest_day) / difference
            plan_occ = float(df.loc[df['days_diff'] == difference]['sum_occupancy_days_ahead'].values[0])
            denom = min(current_occ, plan_occ) if plan_occ != 0 else 1
            x = pace_ratio * (max(current_occ, plan_occ) / denom)

    if x >= 0:
        final_price = x * (max_price - target_price) + target_price
    else:
        final_price = x * (target_price - min_price) + target_price

    final_price = max(min_price, min(final_price, max_price))
    return round(final_price, 2), round(x, 4), min_price, max_price

def send_price_with_retry(apartment_id, date_str, price, retries=3, timeout_sec=10):
    """Αποστολή τιμής με retry + timeout"""
    payload = {
        "apartments": [apartment_id],
        "operations": [
            {"dates": [date_str], "daily_price": price, "min_length_of_stay": 1}
        ]
    }

    for attempt in range(1, retries+1):
        try:
            if TEST_MODE:
                print(f"[TEST] Apartment {apartment_id}, Date {date_str}, Price {price}")
                return

            response = requests.post(API_URL_RATES, json=payload, headers=headers, timeout=timeout_sec)
            response.raise_for_status()
            print(f"✓ Sent {price}€ for {date_str} → Smoobu")
            return
        except requests.exceptions.RequestException as e:
            print(f"⚠ Attempt {attempt} failed sending price for Apt {apartment_id}: {e}")
            time.sleep(2)

    print(f"❌ Failed to send price for Apt {apartment_id} on {date_str} after {retries} attempts")

# -----------------------------
# MAIN LOOP
# -----------------------------
current_datetime = datetime.now()
start = current_datetime.date()
end = start + timedelta(days=190)
current = start

while current <= end:
    date_str = current.strftime("%Y-%m-%d")

    occ, available = get_total_occupancy_with_retry(date_str, APARTMENTS)

    if occ is None or not available:
        print(f"❌ {date_str} | No available apartments or failed to get occupancy")
        current += timedelta(days=1)
        continue

    price, x, min_p, max_p = calculate_price(occ, current, current_datetime)
    if price is None:
        print(f"⚠ {date_str} | Pricing calculation failed")
        current += timedelta(days=1)
        continue

    # Διατήρηση σειράς APARTMENTS για διαθέσιμα
    available_sorted = [apt for apt in APARTMENTS if apt in available]

    if max_p is None:
        # Long-term → όλα ίδια τιμή
        for apt in available_sorted:
            send_price_with_retry(apt, date_str, price)
    else:
        step = (max_p - price) / len(available_sorted) if len(available_sorted) > 0 else 0
        for i, apt in enumerate(available_sorted, start=1):
            price_i = price + (i-1)*step
            price_i = min(price_i, max_p)
            price_i = round(price_i, 1)
            send_price_with_retry(apt, date_str, price_i)

    print(f"✅ {date_str} | Occ={occ:.2f} | x={x} | Base Price={price}")
    current += timedelta(days=1)

print("\nFinished processing all valid dates.")
