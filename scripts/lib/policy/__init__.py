"""Shared SQL policy for current platform verification."""

EFFECTIVE_STATE = """CASE WHEN c.url IS NULL THEN 'untested'
    WHEN c.target IS NOT ? OR c.checked_at>? THEN 'unverified'
    WHEN c.state='available' AND c.valid_until<=? THEN 'expired'
    WHEN c.state='available' AND ?=0 THEN 'disabled' ELSE c.state END"""


def state_args(target, now):
    return [target['fingerprint'], now, now, int(target['enabled'])]
