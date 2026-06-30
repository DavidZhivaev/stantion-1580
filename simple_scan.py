#!/usr/bin/env python3
"""
Simple scanner script - scan sheets from ADF and save as PDF.
No GUI, no ML model, just scan and save.

Usage:
    python3 simple_scan.py
"""

import os
import sys
import tempfile
import uuid
from datetime import datetime

# Import scanner module
try:
    import scanner_hal
except ImportError:
    print("[ERROR] scanner_hal.so not found!")
    print("Build it first: cd scanner_module && sh build_pybind.sh")
    sys.exit(1)

# Import output module
try:
    import output_generator_cpp
except ImportError:
    print("[ERROR] output_generator_cpp.so not found!")
    print("Build it first: cd output_module && sh build_linux.sh")
    sys.exit(1)

from PIL import Image
import numpy as np


def main():
    output_dir = "./output"
    os.makedirs(output_dir, exist_ok=True)

    print("=" * 50)
    print("Simple Scanner - Scan to PDF")
    print("=" * 50)
    print()

    # Create scanner instance
    scanner = scanner_hal.Scanner()

    # List available scanners
    print("[INFO] Searching for scanners...")
    devices = scanner.list_scanners()

    if not devices:
        print("[ERROR] No scanners found!")
        return 1

    print(f"[OK] Found {len(devices)} scanner(s):")
    for i, dev in enumerate(devices):
        print(f"  {i + 1}. {dev}")
    print()

    # Select scanner
    if len(devices) == 1:
        device_id = devices[0]
        print(f"[INFO] Using: {device_id}")
    else:
        try:
            choice = int(input("Select scanner (number): ")) - 1
            device_id = devices[choice]
        except (ValueError, IndexError):
            print("[ERROR] Invalid selection")
            return 1

    # Connect
    print(f"[INFO] Connecting to {device_id}...")
    if not scanner.connect(device_id):
        print("[ERROR] Failed to connect!")
        return 1
    print("[OK] Connected")
    print()

    # Scan batch
    print("[INFO] Scanning... (insert pages into ADF)")
    print("[INFO] Waiting for pages...")

    try:
        arrays = scanner.scan_batch()
    except Exception as e:
        print(f"[ERROR] Scan failed: {e}")
        scanner.disconnect()
        return 1

    if not arrays:
        print("[WARNING] No pages scanned!")
        scanner.disconnect()
        return 0

    print(f"[OK] Scanned {len(arrays)} page(s)")
    print()

    # Disconnect scanner
    scanner.disconnect()
    print("[OK] Scanner disconnected")
    print()

    # Save images to temp files and prepare sheet data
    print("[INFO] Processing images...")
    temp_dir = tempfile.mkdtemp()
    sheets = []

    work_id = str(uuid.uuid4())
    title_barcode = int(datetime.now().strftime("%Y%m%d%H%M%S"))

    for i, arr in enumerate(arrays):
        # Convert numpy array to PIL Image
        img = Image.fromarray(arr)

        # Save to temp file
        img_path = os.path.join(temp_dir, f"page_{i:04d}.jpg")
        img.convert("RGB").save(img_path, "JPEG", quality=92)
        print(f"  Page {i + 1}: {img.size[0]}x{img.size[1]}")

        # Sheet tuple: (path, barcode, type, linked_to, recognized_digits)
        # type: 0=Unknown, 1=Titul, 2=Blan1, 3=Blan2, 4=Additional
        sheet_type = 1 if i == 0 else 0  # First page as Titul
        barcode = title_barcode + i
        sheets.append((img_path, barcode, sheet_type, 0, ""))

    print()

    # Generate PDF
    print("[INFO] Generating PDF...")
    generator = output_generator_cpp.OutputGenerator(output_dir)

    result = generator.create_package(
        work_id=work_id,
        title_barcode=title_barcode,
        sheets=sheets,
        chain_valid=True
    )

    if result["ok"]:
        print(f"[OK] PDF created successfully!")
        print(f"  ZIP: {result['zip_path']}")
        print(f"  PDF: {result['pdf_filename']}")
        print(f"  Pages: {result['sheet_count']}")
    else:
        print(f"[ERROR] Failed to create PDF! Status: {result['status']}")
        return 1

    # Cleanup temp files
    for path, *_ in sheets:
        try:
            os.remove(path)
        except:
            pass
    try:
        os.rmdir(temp_dir)
    except:
        pass

    print()
    print("=" * 50)
    print("Done!")
    print("=" * 50)
    return 0


if __name__ == "__main__":
    sys.exit(main())
