#!/usr/bin/env python
# -*- coding: utf-8 -*-

import requests
import pandas as pd
from datetime import datetime, timedelta
import os
import time

# =====================================================
# CONFIG
# =====================================================
API_URL_AVAIL = "https://login.smoobu.com/booking/checkApartmentAvailability"
API_URL_RATES = "https://login.smoobu.com/api/rates"

CUSTOMER_ID = int(os.getenv("SMOOBU_CUSTOMER_ID"))
API_KEY = os.getenv("SMOOBU_API_KEY")

# ΣΕΙΡΑ ΚΑΤΑΛΥΜΑΤΩΝ (αυτή η σειρά καθορίζει και τις τιμές)
APARTMENTS = [
    1439913, 1439915, 1439917, 1439919, 1439921, 1439923, 1439925, 1439927,
    1439929, 1439931, 1439933, 1439935, 1439937, 1439939,
    1439971, 1439973, 1439975, 1439977, 1439979, 1439981, 1439983, 1439985
]

# Ελάχιστη τιμή για ΣΗΜΕΡΑ ανά μήνα
MIN_PRICE_SAME_DAY_BY_MONTH = {
    1: 50, 2: 50, 3: 55, 4: 60,
    5: 70, 6: 80, 7: 80, 8: 80,
    9: 80, 10: 70, 11: 50, 12: 50
}

TEST_MODE = False  # True = δεν στέλνει στο Smoobu

# =====================================================
# LOAD EXCEL
# =====================================================
df = pd.read_excel("data_zed.xlsx")
df["date"] = pd.to_datetime(df["date"]).dt.date

headers = {
    "Api-Key": API_KEY,
    "Content-Type": "application/json"
}

# =====================================================
# AVAILABILITY (με retry + timeout)
# =====================================================
def get_total_occupancy(date_str, apartment_ids, retries=3, timeout=10):
    """
    Παίρνει συνολική πληρότητα & διαθέσιμα καταλύματα
    - retry: πόσες φορές αν αποτύχει
    - timeout: πόσα δευτερόλεπτα περιμένει
    """
    for attempt in range(1, retries + 1):
        try:
            payload = {
                "arrivalDate": date_str,
                "departureDate": (
                    datetime.strptime(date_str, "%Y-%m-%d") + timedelta(days=1)
                ).strftime("%Y-%m-%d"),
                "apartments": apartment_ids,
                "customerId": CUSTOMER_ID
            }

            r = requests.post(API_URL_AVAIL, json=payload, headers=headers, timeout=timeout)
            r.raise_for_status()

            data = r.json()
            available = data.get("availableApartments", [])
            occupied = len(apartment_ids) - len(available)
            occ = occupied / len(apartment_ids)

            return occ, available

        except requests.exceptions.RequestException as e:
            print(f"⚠ Availability attempt {attempt} failed for {date_str}: {e}")
            time.sleep(2)

    print(f"❌ Availability failed for {date_str}")
    return None, []

# =====================================================
# PRICE CALCULATION
# =====================================================
def calculate_price(current_occ, target_date, now):
    """
    Υπολογίζει:
    - τελική base τιμή
    - x (composite score)
    - min / max τιμές
    """

    difference = (target_date - now.date()).days

    # Εκτός ορίων
    if difference < 0 or difference > 365:
        return None, None, None, None

    row = df.loc[df["date"] == target_date]
    if row.empty:
        return None, None, None, None

    target_price = float(row["target_price"].iloc[0])
    max_price = float(row["max_price"].iloc[0])

    # =================================================
    # 🔴 ΠΕΡΙΠΤΩΣΗ 1: ΣΗΜΕΡΑ → ΩΡΙΑΙΑ ΛΟΓΙΚΗ
    # =================================================
    if difference == 0:
        min_price = MIN_PRICE_SAME_DAY_BY_MONTH[target_date.month]

        current_hour = now.hour
        hours_left = max(1, 23 - current_hour)

        if current_occ == 0:
            # Καθόλου κρατήσεις → μόνο pacing
            x = (hours_left - 263) / hours_left
        else:
            # Σύγκριση με historical occupancy
            temp = df.copy()
            temp["diff_occ"] = abs(temp["sum_occupancy_days_ahead"] - current_occ)
            closest = temp.loc[temp["diff_occ"].idxmin()]

            plan_hour = int(closest["hours_diff"])
            pace_ratio = (hours_left - plan_hour) / hours_left

            if hours_left in df["hours_diff"].values:
                plan_occ = float(
                    df.loc[df["hours_diff"] == hours_left]["sum_occupancy_days_ahead"].values[0]
                )
            else:
                plan_occ = current_occ

            denom = min(current_occ, plan_occ) if plan_occ > 0 else 1
            occupancy_ratio = max(current_occ, plan_occ) / denom

            x = pace_ratio * occupancy_ratio

        # Μετατροπή score → τιμή
        if x >= 0:
            price = x * (max_price - target_price) + target_price
        else:
            price = x * (target_price - min_price) + target_price

        price = max(min_price, min(price, max_price))
        return round(price, 2), round(x, 4), min_price, max_price

    # =================================================
    # 🟢 ΠΕΡΙΠΤΩΣΗ 2: ΜΕΛΛΟΝΤΙΚΕΣ ΜΕΡΕΣ → ΗΜΕΡΕΣ
    # =================================================
    min_price = float(row["min_price"].iloc[0])

    # Long-term strategy
    if difference > 240:
        return round(target_price + 20, 2), None, None, None

    if current_occ == 0:
        x = (difference - 240) / difference
    else:
        temp = df.copy()
        temp["diff_occ"] = abs(temp["sum_occupancy_days_ahead"] - current_occ)
        closest = temp.loc[temp["diff_occ"].idxmin()]

        plan_day = int(closest["days_diff"])
        pace_ratio = (difference - plan_day) / difference

        if difference in df["days_diff"].values:
            plan_occ = float(
                df.loc[df["days_diff"] == difference]["sum_occupancy_days_ahead"].values[0]
            )
        else:
            plan_occ = current_occ

        denom = min(current_occ, plan_occ) if plan_occ > 0 else 1
        occupancy_ratio = max(current_occ, plan_occ) / denom

        x = pace_ratio * occupancy_ratio

    if x >= 0:
        price = x * (max_price - target_price) + target_price
    else:
        price = x * (target_price - min_price) + target_price

    price = max(min_price, min(price, max_price))
    return round(price, 2), round(x, 4), min_price, max_price

# =====================================================
# SEND PRICE (με retry + timeout)
# =====================================================
def send_price(apartment_id, date_str, price, retries=3, timeout=10):
    payload = {
        "apartments": [apartment_id],
        "operations": [{
            "dates": [date_str],
            "daily_price": price,
            "min_length_of_stay": 1
        }]
    }

    for attempt in range(1, retries + 1):
        try:
            if TEST_MODE:
                print(f"[TEST] {date_str} | Apt {apartment_id} → {price}")
                return

            r = requests.post(API_URL_RATES, json=payload, headers=headers, timeout=timeout)
            r.raise_for_status()
            print(f"✓ Sent {price}€ for {date_str} → Smoobu")
            return

        except requests.exceptions.RequestException as e:
            print(f"⚠ Send attempt {attempt} failed for Apt {apartment_id}: {e}")
            time.sleep(2)
    print(f"❌ Failed to send price for Apt {apartment_id} on {date_str} after {retries} attempts")         

# =====================================================
# MAIN LOOP
# =====================================================
now = datetime.now()
current = now.date()
end = current + timedelta(days=90)

while current <= end:
    date_str = current.strftime("%Y-%m-%d")

    occ, available = get_total_occupancy(date_str, APARTMENTS)
    if occ is None or not available:
        print(f"❌ {date_str} | No available apartments or failed to get occupancy")
        current += timedelta(days=1)
        continue

    price, x, min_p, max_p = calculate_price(occ, current, now)
    if price is None:
        print(f"⚠ {date_str} | Pricing calculation failed")
        current += timedelta(days=1)
        continue

    # Κρατάμε μόνο διαθέσιμα και με σειρά APARTMENTS
    available_sorted = [apt for apt in APARTMENTS if apt in available]

    if max_p is None:
        # Long-term → ίδια τιμή για όλα τα διαθέσιμα
        for apt in available_sorted:
            print(f"[TEST] {date_str} | Apt {apt} → {price}")  # εκτύπωση
            send_price(apt, date_str, price)
    else:
        step = (max_p - price) / len(available_sorted) if len(available_sorted) > 0 else 0
        for i, apt in enumerate(available_sorted, start=1):
            price_i = price + (i-1)*step
            price_i = min(price_i, max_p)
            price_i = round(price_i, 1)
            print(f"[TEST] {date_str} | Apt {apt} → {price_i}")  # εκτύπωση
            send_price(apt, date_str, price_i)

    # Εκτύπωση συνολικής πληρότητας, x και base price
    print(f"✅ {date_str} | Occ={occ:.4f} | x={x} | Base={price}")
    current += timedelta(days=1)

print("\nFinished processing all valid dates.")

