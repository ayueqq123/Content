"""Hardware fingerprint generation for license binding."""

import hashlib
import platform
import uuid


def get_hwid() -> str:
    """Generate a hardware fingerprint based on machine identifiers."""
    parts = []

    # MAC address
    mac = uuid.getnode()
    parts.append(f"MAC:{mac}")

    # Platform info
    parts.append(f"SYS:{platform.system()}")
    parts.append(f"NODE:{platform.node()}")
    parts.append(f"PROC:{platform.processor()}")

    # Try to get more hardware info on Windows
    if platform.system() == "Windows":
        try:
            import subprocess
            result = subprocess.run(
                ["wmic", "diskdrive", "get", "serialnumber"],
                capture_output=True, text=True, timeout=5,
            )
            serial = result.stdout.strip().split("\n")[-1].strip()
            if serial and serial != "SerialNumber":
                parts.append(f"DISK:{serial}")
        except Exception:
            pass

        try:
            import subprocess
            result = subprocess.run(
                ["wmic", "cpu", "get", "processorid"],
                capture_output=True, text=True, timeout=5,
            )
            cpuid = result.stdout.strip().split("\n")[-1].strip()
            if cpuid and cpuid != "ProcessorId":
                parts.append(f"CPU:{cpuid}")
        except Exception:
            pass

    raw = "|".join(parts)
    digest = hashlib.sha256(raw.encode()).hexdigest()[:32].upper()
    # Format as XXXX-XXXX-XXXX-XXXX
    return "-".join(digest[i:i + 8] for i in range(0, 32, 8))
