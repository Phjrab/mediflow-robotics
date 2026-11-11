# Reused check_three_cameras.py allowed_owners SHA256 e860035412945f35ed25d739e82f11c4a5a292f5d53866385b2d0cc605c9a996
def allowed_owners(code, stdout, stderr, astra=False, group_reader=None):
    if not astra:
        return code == 1 and not stdout.strip() and not stderr.strip()
    ids = stdout.split()
    if code != 0 or not ids or not all(pid.isdigit() for pid in ids):
        return False
    if any(word in stderr.lower() for word in ('password', 'permission', 'cannot', 'no such', 'denied')):
        return False
    try:
        return all(any(line.endswith('/astra-v4l2-bridge.service') for line in group_reader(pid).splitlines()) for pid in ids)
    except (OSError, ValueError, TypeError):
        return False
