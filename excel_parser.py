import pandas as pd
import datetime
import os

from ukg_parser import parse_ukg_roster

def parse_roster(file_path, target_name):
    """
    Parses the Excel roster and returns a list of shift events for the target_name.
    Each event is a dict: {'start': datetime, 'end': datetime, 'summary': str}
    """
    # 1. Try the new robust UKG Pro parser first (highly flexible and generic)
    print(f"Versuche UKG Pro Parser für {file_path}...")
    try:
        shifts = parse_ukg_roster(file_path, target_name)
        if shifts:
            print(f"Erfolg mit UKG Pro Parser: {len(shifts)} Schichten gefunden.")
            return shifts
    except Exception as e:
        print(f"UKG Pro Parser Fehler: {e}")

    # 2. Fallback to original custom parser
    print(f"Fallback auf alten Dienstplan-Parser für {file_path}...")
    try:
        xl = pd.ExcelFile(file_path)
        print(f"Sheets found: {xl.sheet_names}")
    except Exception as e:
        print(f"Error opening Excel file: {e}")
        return []

    all_shifts = []
    
    # Process all sheets (months)
    for sheet_name in xl.sheet_names:
        try:
            df = pd.read_excel(file_path, sheet_name=sheet_name)
        except Exception:
            continue



        if len(df) < 5:
            continue

        # Search for the name in the first few rows
        start_col = -1
        for row_idx in range(min(10, len(df))):
            row_vals = df.iloc[row_idx]
            for col_idx, val in enumerate(row_vals):
                if pd.notna(val) and target_name.lower() in str(val).lower():
                    start_col = col_idx
                    break
            if start_col != -1:
                break
        
        if start_col == -1:
            continue

        # Get year from file name or current year
        import re
        year_match = re.search(r'20\d{2}', os.path.basename(file_path))
        year = int(year_match.group(0)) if year_match else datetime.datetime.now().year

        month_map = {
            'Januar': 1, 'Februar': 2, 'März': 3, 'April': 4, 'Mai': 5, 'Juni': 6,
            'Juli': 7, 'August': 8, 'September': 9, 'Oktober': 10, 'November': 11, 'Dezember': 12
        }
        # Try to find month in sheet name
        month_num = 0
        for m_name, m_num in month_map.items():
            if m_name.lower() in sheet_name.lower():
                month_num = m_num
                break
        
        if month_num == 0:
            continue

        for row_idx in range(4, len(df)):
            row = df.iloc[row_idx]
            if len(row) < start_col + 4:
                continue
                
            day_num_str = str(row.iloc[start_col + 1]).replace('.', '').strip()
            if not day_num_str.isdigit():
                continue
            
            day = int(day_num_str)
            start_time_val = row.iloc[start_col + 2]
            end_time_val = row.iloc[start_col + 3]
            shift_type = str(row.iloc[start_col + 5]) if len(row) > start_col + 5 and pd.notna(row.iloc[start_col + 5]) else ""

            if pd.isna(start_time_val) or pd.isna(end_time_val):
                continue

            try:
                if isinstance(start_time_val, datetime.time):
                    start_time = start_time_val
                else:
                    start_time = datetime.datetime.strptime(str(start_time_val), "%H:%M:%S").time()
                
                if isinstance(end_time_val, datetime.time):
                    end_time = end_time_val
                else:
                    end_time = datetime.datetime.strptime(str(end_time_val), "%H:%M:%S").time()

                start_dt = datetime.datetime(year, month_num, day, start_time.hour, start_time.minute)
                
                if end_time < start_time:
                    end_dt = datetime.datetime(year, month_num, day, end_time.hour, end_time.minute) + datetime.timedelta(days=1)
                else:
                    end_dt = datetime.datetime(year, month_num, day, end_time.hour, end_time.minute)

                summary = f"Dienst ({shift_type})" if shift_type else "Dienst"
                all_shifts.append({
                    'start': start_dt,
                    'end': end_dt,
                    'summary': summary
                })
            except Exception:
                continue
                
    return all_shifts

