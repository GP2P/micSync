from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import re


RECORDING_NAME_RE = re.compile(
    r"^(?P<tx_slot>TX\d{2})_(?P<mic_sequence>MIC\d{3})_"
    r"(?P<date>\d{8})_(?P<time>\d{6})(?:_(?P<variant>orig|edit))?\.wav$"
)

VOLUME_LABEL_RE = re.compile(r"^MIC\s*(?P<mic_id>\d+)$", re.IGNORECASE)


@dataclass(frozen=True)
class ParsedRecordingName:
    tx_slot: str
    mic_sequence: str
    start_at: datetime
    variant: str | None
    recording_group_key: str
    dest_name: str


def parse_recording_name(filename: str) -> ParsedRecordingName:
    match = RECORDING_NAME_RE.match(filename)
    if match is None:
        raise ValueError(f"unsupported recording filename: {filename}")

    tx_slot = match.group("tx_slot")
    mic_sequence = match.group("mic_sequence")
    date_part = match.group("date")
    time_part = match.group("time")
    variant = match.group("variant")
    start_at = datetime.strptime(f"{date_part}_{time_part}", "%Y%m%d_%H%M%S")
    recording_group_key = f"{date_part}_{time_part}_{tx_slot}_{mic_sequence}"
    suffix = f"_{variant}" if variant else ""
    dest_name = f"{date_part}_{time_part}_{tx_slot}_{mic_sequence}{suffix}.wav"
    return ParsedRecordingName(
        tx_slot=tx_slot,
        mic_sequence=mic_sequence,
        start_at=start_at,
        variant=variant,
        recording_group_key=recording_group_key,
        dest_name=dest_name,
    )


def parse_physical_mic_id(volume_label: str | None) -> int:
    if not volume_label:
        return 0
    match = VOLUME_LABEL_RE.match(volume_label.strip())
    if match is None:
        return 0
    return int(match.group("mic_id"))
