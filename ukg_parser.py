import pandas as pd
import datetime
import os
import re

def parse_ukg_roster(file_path, target_name):
    """
    Parses a UKG Pro Excel schedule report.
    Returns a list of dicts: {'start': datetime, 'end': datetime, 'summary': str}
    """
    all_shifts = []
    
    try:
        sheets_data = {}
        with pd.ExcelFile(file_path) as xl:
            print(f"[UKG Parser] sheets: {xl.sheet_names}")
            for sheet_name in xl.sheet_names:
                try:
                    sheets_data[sheet_name] = pd.read_excel(xl, sheet_name=sheet_name)
                except Exception as e:
                    print(f"[UKG Parser] Error reading sheet {sheet_name}: {e}")
                    continue
    except Exception as e:
        print(f"[UKG Parser] Error opening Excel file: {e}")
        return []

    # Get default year from filename or current year
    year_match = re.search(r'20\d{2}', os.path.basename(file_path))
    default_year = int(year_match.group(0)) if year_match else datetime.datetime.now().year

    for sheet_name, df in sheets_data.items():
        if len(df) == 0:
            continue

        # Clean column names for matching
        df.columns = [str(c).strip() for c in df.columns]

        header_row_idx = -1
        cols_to_use = {}
        
        # Search first 15 rows for the headers
        for r_idx in range(min(15, len(df))):
            row_vals = [str(x).lower().strip() for x in df.iloc[r_idx]]
            
            date_col = -1
            start_col = -1
            end_col = -1
            name_col = -1
            shift_col = -1
            
            for col_idx, val in enumerate(row_vals):
                if any(x in val for x in ["date", "datum", "tag"]):
                    date_col = col_idx
                elif any(x in val for x in ["start time", "schedule start", "shift start", "beginn", "von", "start"]):
                    start_col = col_idx
                elif any(x in val for x in ["end time", "schedule end", "shift end", "ende", "bis", "end"]):
                    end_col = col_idx
                elif any(x in val for x in ["employee name", "employee", "mitarbeiter", "name"]):
                    name_col = col_idx
                elif any(x in val for x in ["shift label", "shift", "job", "paycode", "code", "role"]):
                    shift_col = col_idx
            
            # If we found at least Date and Start/End, we assume this is the header row
            if date_col != -1 and start_col != -1 and end_col != -1:
                header_row_idx = r_idx
                cols_to_use = {
                    "date": date_col,
                    "start": start_col,
                    "end": end_col,
                    "name": name_col,
                    "shift": shift_col
                }
                break

        # If no header row found in the cells, look at the dataframe columns
        if header_row_idx == -1:
            row_vals = [str(x).lower().strip() for x in df.columns]
            date_col = -1
            start_col = -1
            end_col = -1
            name_col = -1
            shift_col = -1
            
            for col_idx, val in enumerate(row_vals):
                if any(x in val for x in ["date", "datum", "tag"]):
                    date_col = col_idx
                elif any(x in val for x in ["start time", "schedule start", "shift start", "beginn", "von", "start"]):
                    start_col = col_idx
                elif any(x in val for x in ["end time", "schedule end", "shift end", "ende", "bis", "end"]):
                    end_col = col_idx
                elif any(x in val for x in ["employee name", "employee", "mitarbeiter", "name"]):
                    name_col = col_idx
                elif any(x in val for x in ["shift label", "shift", "job", "paycode", "code", "role"]):
                    shift_col = col_idx

            if date_col != -1 and start_col != -1 and end_col != -1:
                header_row_idx = -2
                cols_to_use = {
                    "date": date_col,
                    "start": start_col,
                    "end": end_col,
                    "name": name_col,
                    "shift": shift_col
                }

        if not cols_to_use:
            continue

        print(f"[UKG Parser] Found headers. Mapping: {cols_to_use}")

        # Start parsing rows
        start_row = header_row_idx + 1 if header_row_idx >= 0 else 0
        for r_idx in range(start_row, len(df)):
            row = df.iloc[r_idx]
            
            # Name filter if name column exists
            if cols_to_use["name"] != -1 and target_name:
                name_val = str(row.iloc[cols_to_use["name"]])
                if pd.isna(row.iloc[cols_to_use["name"]]) or target_name.lower() not in name_val.lower():
                    continue

            # Parse date
            date_val = row.iloc[cols_to_use["date"]]
            if pd.isna(date_val):
                continue
            
            # Parse start and end times
            start_time_val = row.iloc[cols_to_use["start"]]
            end_time_val = row.iloc[cols_to_use["end"]]
            if pd.isna(start_time_val) or pd.isna(end_time_val):
                continue

            try:
                if isinstance(date_val, (datetime.datetime, datetime.date)):
                    parsed_date = date_val
                else:
                    date_str = str(date_val).strip()
                    match_de = re.match(r'(\d{1,2})\.(\d{1,2})\.?(\d{2,4})?', date_str)
                    if match_de:
                        day = int(match_de.group(1))
                        month = int(match_de.group(2))
                        year = int(match_de.group(3)) if match_de.group(3) else default_year
                        if year < 100: year += 2000
                        parsed_date = datetime.date(year, month, day)
                    else:
                        parsed_date = pd.to_datetime(date_str).date()

                def parse_time(t_val):
                    if isinstance(t_val, datetime.time):
                        return t_val
                    elif isinstance(t_val, datetime.datetime):
                        return t_val.time()
                    else:
                        t_str = str(t_val).strip()
                        for fmt in ["%H:%M:%S", "%H:%M", "%H.%M.%S", "%H.%M"]:
                            try:
                                return datetime.datetime.strptime(t_str, fmt).time()
                            except ValueError:
                                continue
                        return pd.to_datetime(t_str).time()

                start_time = parse_time(start_time_val)
                end_time = parse_time(end_time_val)

                start_dt = datetime.datetime.combine(parsed_date, start_time)
                
                if end_time < start_time:
                    end_dt = datetime.datetime.combine(parsed_date, end_time) + datetime.timedelta(days=1)
                else:
                    end_dt = datetime.datetime.combine(parsed_date, end_time)

                shift_label = ""
                if cols_to_use["shift"] != -1 and pd.notna(row.iloc[cols_to_use["shift"]]):
                    shift_label = str(row.iloc[cols_to_use["shift"]]).strip()
                
                summary = f"Dienst ({shift_label})" if shift_label else "Dienst"
                
                all_shifts.append({
                    'start': start_dt,
                    'end': end_dt,
                    'summary': summary
                })
            except Exception as e:
                continue

    print(f"[UKG Parser] Extracted {len(all_shifts)} shifts.")
    return all_shifts
