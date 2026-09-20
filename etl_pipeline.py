"""
ETL Pipeline: Port & Vessel Operations
----------------------------------------
Extracts raw operations data from CSV, validates it, transforms it into a
normalized relational schema (Ports, Vessels, Operations), and loads it
into a SQLite database that Power BI connects to directly.

Run:
    python etl_pipeline.py

Output:
    port_vessel_operations.db   (SQLite database)
    validation_report.json      (data quality report, consumed by ai_agent.py)
"""

import pandas as pd
import sqlite3
import json
import sys
from datetime import datetime

SOURCE_CSV = "port_vessel_operations.csv"
DB_FILE = "port_vessel_operations.db"
VALIDATION_REPORT = "validation_report.json"


def extract(path: str) -> pd.DataFrame:
    print(f"[EXTRACT] Reading {path} ...")
    df = pd.read_csv(path, parse_dates=["Date"])
    print(f"[EXTRACT] Loaded {len(df)} rows.")
    return df


def validate(df: pd.DataFrame) -> dict:
    """Run data quality checks and return a structured report.
    Raises if any CRITICAL check fails (bad data should not silently load)."""
    print("[VALIDATE] Running data quality checks ...")
    report = {"timestamp": datetime.now().isoformat(), "row_count": len(df), "checks": []}

    def check(name, passed, detail, critical=True):
        report["checks"].append({
            "check": name, "passed": bool(passed), "detail": detail, "critical": critical
        })
        status = "PASS" if passed else ("FAIL" if critical else "WARN")
        print(f"  [{status}] {name}: {detail}")

    # 1. Required columns present
    required_cols = ["Date", "VesselID", "Port", "Region", "VesselType",
                      "CargoVolume_Tons", "ActualDelay_Min", "DwellTime_Hours", "OnTime"]
    missing = [c for c in required_cols if c not in df.columns]
    check("required_columns_present", len(missing) == 0, f"Missing: {missing}" if missing else "All present")

    # 2. No nulls in key columns
    for col in ["VesselID", "Port", "Date"]:
        n_null = df[col].isna().sum()
        check(f"no_nulls_{col}", n_null == 0, f"{n_null} null value(s)")

    # 3. VesselID uniqueness (each row should be a unique port call, not a duplicate record)
    dupes = df.duplicated(subset=["VesselID", "Date", "Port"]).sum()
    check("no_duplicate_operations", dupes == 0, f"{dupes} duplicate row(s)", critical=False)

    # 4. Value ranges sane
    neg_cargo = (df["CargoVolume_Tons"] < 0).sum()
    check("cargo_volume_non_negative", neg_cargo == 0, f"{neg_cargo} negative value(s)")

    neg_delay = (df["ActualDelay_Min"] < 0).sum()
    check("delay_non_negative", neg_delay == 0, f"{neg_delay} negative value(s)")

    bad_dwell = ((df["DwellTime_Hours"] <= 0) | (df["DwellTime_Hours"] > 200)).sum()
    check("dwell_time_in_expected_range", bad_dwell == 0, f"{bad_dwell} out-of-range value(s)", critical=False)

    # 5. Categorical integrity
    valid_ports = {"Mumbai", "Chennai", "Kolkata", "Kandla", "Visakhapatnam", "Cochin"}
    bad_ports = (~df["Port"].isin(valid_ports)).sum()
    check("port_names_recognized", bad_ports == 0, f"{bad_ports} unrecognized port name(s)")

    # 6. Date range sanity
    date_ok = df["Date"].between("2020-01-01", "2035-12-31").all()
    check("dates_within_sane_range", date_ok, "All dates between 2020-2035")

    n_failed_critical = sum(1 for c in report["checks"] if c["critical"] and not c["passed"])
    report["critical_failures"] = n_failed_critical
    report["status"] = "FAILED" if n_failed_critical > 0 else "PASSED"

    with open(VALIDATION_REPORT, "w") as f:
        json.dump(report, f, indent=2)
    print(f"[VALIDATE] Report saved to {VALIDATION_REPORT} — overall status: {report['status']}")

    if n_failed_critical > 0:
        raise ValueError(f"Validation failed with {n_failed_critical} critical error(s). Aborting load.")

    return report


def transform(df: pd.DataFrame):
    """Normalize the flat CSV into 3 related tables (relational modelling)."""
    print("[TRANSFORM] Building relational schema ...")

    ports = (df[["Port", "Region"]]
             .drop_duplicates()
             .reset_index(drop=True))
    ports.insert(0, "PortID", range(1, len(ports) + 1))

    vessels = (df[["VesselID", "VesselType"]]
               .drop_duplicates()
               .reset_index(drop=True))

    ops = df.merge(ports, on=["Port", "Region"]).drop(columns=["Port", "Region"])
    ops = ops.rename(columns={"VesselID": "VesselID_fk"})
    ops.insert(0, "OperationID", range(1, len(ops) + 1))
    ops = ops.rename(columns={"VesselID_fk": "VesselID"})

    # Keep only the FK + measures in the operations table
    ops = ops[["OperationID", "Date", "VesselID", "PortID", "CargoVolume_Tons",
               "PredictedDelay_Min", "ActualDelay_Min", "DwellTime_Hours",
               "FuelConsumption_Tons", "OnTime"]]

    print(f"[TRANSFORM] Ports: {len(ports)} | Vessels: {len(vessels)} | Operations: {len(ops)}")
    return ports, vessels, ops


def load(ports: pd.DataFrame, vessels: pd.DataFrame, ops: pd.DataFrame, db_path: str):
    print(f"[LOAD] Writing to {db_path} ...")
    conn = sqlite3.connect(db_path)
    ports.to_sql("Ports", conn, if_exists="replace", index=False)
    vessels.to_sql("Vessels", conn, if_exists="replace", index=False)
    ops.to_sql("Operations", conn, if_exists="replace", index=False)

    conn.execute("CREATE INDEX IF NOT EXISTS idx_ops_port ON Operations(PortID)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_ops_vessel ON Operations(VesselID)")
    conn.commit()
    conn.close()
    print("[LOAD] Done.")


def main():
    try:
        df = extract(SOURCE_CSV)
        validate(df)
        ports, vessels, ops = transform(df)
        load(ports, vessels, ops, DB_FILE)
        print("\nPipeline completed successfully.")
    except Exception as e:
        print(f"\nPipeline FAILED: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
