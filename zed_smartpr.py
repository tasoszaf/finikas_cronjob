#!/usr/bin/env python
# -*- coding: utf-8 -*-

import requests
import pandas as pd
from datetime import datetime, timedelta
import os


# -----------------------------
#   CONFIG
# -----------------------------
API_URL_AVAIL = "https://login.smoobu.com/booking/checkApartmentAvailability"
API_URL_RATES = "https://login.smoobu.com/api/rates"

# Διαβάζει Secrets από GitHub Actions
CUSTOMER_ID = int(os.getenv("SMOOBU_CUSTOMER_ID"))
API_KEY = os.getenv("SMOOBU_API_KEY")


APARTMENTS = [
    1439913, 1439915, 1439917, 1439919, 1439921, 1439923, 1439925, 1439927,
    1439929, 1439931, 1439933, 1439935, 1439937, 1439939, 1439971, 1439973,
    1439975, 1439977, 1439979, 1439981, 1439983, 1439985
]

MIN_PRICE_SAME_DAY_BY_MONTH = {
    1: 50, 2: 50, 3: 55, 4: 60, 5: 70, 6: 80,
    7: 80, 8: 80, 9: 80, 10: 70, 11: 50, 12: 50
}

TOTAL_ROOMS = len(APARTMENTS)
TEST_MODE = True  # True = εμφανίζει τιμές, False = στέλνει στο Smoobu

# Excel
df = pd.read_excel("/Users/anastasioszafeiriou/Desktop/data_zed.xlsx")
df['date'] = pd.to_datetime(df['date'])  # ασφαλής σύγκριση ημερομηνιών

headers = {
    "Api-Key": API_KEY,
    "Content-Type": "application/json"
}

# -----------------------------
#   ΒΟΗΘΗΤΙΚΕΣ ΣΥΝΑΡΤΗΣΕΙΣ
# -----------------------------
def get_total_occupancy(date_str, apartment_ids):
    """Παίρνει την τρέχουσα πληρότητα από Smoobu για συγκεκριμένη ημερομηνία"""
    arrival = date_str
    departure = (datetime.strptime(date_str, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
    payload = {
        "arrivalDate": arrival,
        "departureDate": departure,
        "apartments": apartment_ids,
        "customerId": CUSTOMER_ID
    }

    response = requests.post(API_URL_AVAIL, json=payload, headers=headers)
    response.raise_for_status()
    data = response.json()

    available_apts = data.get("availableApartments", [])
    occupied_count = len(apartment_ids) - len(available_apts)
    total_occ = occupied_count / len(apartment_ids)
    return total_occ, available_apts

def calculate_price(current_occ, target_date, current_datetime):
    """Υπολογίζει τελική τιμή και composite score"""
    difference = (target_date - current_datetime.date()).days

    if difference < 0 or difference > 365:
        return None, None, None, None

    row_price = df.loc[df['date'] == target_date.strftime("%m/%d/%Y")]
    if row_price.empty:
        return None, None, None, None

    target_price = float(row_price['target_price'].iloc[0])
    max_price = float(row_price['max_price'].iloc[0])
    min_price = MIN_PRICE_SAME_DAY_BY_MONTH[target_date.month] if difference == 0 else float(row_price['min_price'].iloc[0])

    # -----------------------------
    #   LONG-TERM (>240 ημέρες)
    # -----------------------------
    if difference > 240:
        final_price = target_price + 20
        return round(final_price, 2), None, None, None  # όλα τα δωμάτια ίδια τιμή

    # -----------------------------
    #   COMPOSITE SCORE ΓΙΑ difference <= 240
    # -----------------------------
    if difference == 0:
        current_hour = current_datetime.hour
        hours_left = 23 - current_hour

        if current_occ == 0:
            pace_ratio = (hours_left - 263) / hours_left
            x = pace_ratio
            occupancy_ratio = None
        else:
            temp_df = df.copy()
            temp_df['diff_occ'] = abs(temp_df['sum_occupancy_days_ahead'] - current_occ)
            closest_row = temp_df.loc[temp_df['diff_occ'].idxmin()]
            closest_plan_hour = int(closest_row['hours_diff'])
            pace_ratio = (hours_left - closest_plan_hour) / hours_left

            plan_occ_at_hour = float(df.loc[df['hours_diff'] == hours_left]['sum_occupancy_days_ahead'].values[0])
            denom = min(current_occ, plan_occ_at_hour) if plan_occ_at_hour != 0 else 1
            occupancy_ratio = max(current_occ, plan_occ_at_hour) / denom if denom != 0 else 1
            x = pace_ratio * occupancy_ratio
    else:
        if current_occ == 0:
            pace_ratio = (difference - 240) / difference
            x = pace_ratio
            occupancy_ratio = None
        else:
            temp_df = df.copy()
            temp_df['diff_occ'] = abs(temp_df['sum_occupancy_days_ahead'] - current_occ)
            closest_row = temp_df.loc[temp_df['diff_occ'].idxmin()]
            closest_plan_day = int(closest_row['days_diff'])
            pace_ratio = (difference - closest_plan_day) / difference

            plan_occ_at_diff = float(df.loc[df['days_diff'] == difference]['sum_occupancy_days_ahead'].values[0])
            denom = min(current_occ, plan_occ_at_diff) if plan_occ_at_diff != 0 else 1
            occupancy_ratio = max(current_occ, plan_occ_at_diff) / denom if denom != 0 else 1
            x = pace_ratio * occupancy_ratio

    if x >= 0:
        final_price = x * (max_price - target_price) + target_price
    else:
        final_price = x * (target_price - min_price) + target_price

    final_price = max(min_price, min(final_price, max_price))
    return round(final_price, 2), round(x, 4), min_price, max_price

# -----------------------------
#   ΑΠΟΣΤΟΛΗ ΤΙΜΗΣ ΣΤΟ SMOOBU
# -----------------------------
def send_price_to_smoobu(apartment_id, date_str, price):
    payload = {
        "apartments": [apartment_id],
        "operations": [
            {
                "dates": [date_str],
                "daily_price": price,
                "min_length_of_stay": 1
            }
        ]
    }

    if TEST_MODE:
        print(f"[TEST MODE] Apartment {apartment_id}, Date {date_str}, Price {price}")
        return

    response = requests.post(API_URL_RATES, headers=headers, json=payload)
    response.raise_for_status()
    print(f"✓ Sent price {price} for {date_str} → Smoobu")
# -----------------------------
#   MAIN LOOP ΓΙΑ 2026
# -----------------------------
current_datetime = datetime.now()
start = datetime.now().date()
end = start + timedelta(days=210) 
current = start

while current <= end:
    date_str = current.strftime("%Y-%m-%d")

    try:
        total_occ, available_apts = get_total_occupancy(date_str, APARTMENTS)
    except Exception as e:
        print(f"⚠ Σφάλμα στο get_total_occupancy για {date_str}: {e}")
        current += timedelta(days=1)
        continue

    price, x, min_price, max_price = calculate_price(
        current_occ=total_occ,
        target_date=current,
        current_datetime=current_datetime
    )

    if price is None:
        print(f"⚠ Παραλείπεται {date_str} (εκτός 0–365 ή δεν υπάρχει στο Excel)")
        current += timedelta(days=1)
        continue

    if not available_apts:
        print(f"❌ Δεν υπάρχουν διαθέσιμα δωμάτια για {date_str}")
        current += timedelta(days=1)
        continue

    available_apts_sorted = [apt for apt in APARTMENTS if apt in available_apts]
    available_rooms = len(available_apts_sorted)

    # -----------------------------
    #   ΔΙΑΝΟΜΗ ΤΙΜΩΝ
    # -----------------------------
    if min_price is None or max_price is None:
        # long-term >240 μέρες → όλα ίδια τιμή
        for apt in available_apts_sorted:
            send_price_to_smoobu(apt, date_str, price)
    else:
        # normal calculation → διανομή με step
        step = (max_price - price) / available_rooms if available_rooms > 0 else 0
        for i, apt in enumerate(available_apts_sorted, start=1):
            price_i = price + (i - 1) * step
            price_i = min(price_i, max_price)
            price_i = round(price_i, 1)
            send_price_to_smoobu(apt, date_str, price_i)

    print(f"✅ Date {date_str}, Total Occupancy {total_occ:.2f}, x={x}, Base Price {price}")
    current += timedelta(days=1)

print("\nFinished processing all valid dates of 2026.")
