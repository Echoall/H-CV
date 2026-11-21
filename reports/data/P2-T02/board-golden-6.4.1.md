# P2-T02 K230 ulab 6.4.1 golden comparison

- Board firmware: CanMV v1.4-19-ga7de1c8, MicroPython 1.21.0.
- Board ulab: 6.4.1-(4)D-c, from the existing P0-EXP00 inventory.
- Script: `tools/board/p2_t02_golden.py`.
- The script uses three profile cases, two marker cases, and two geometry cases. It asserts found/position, marker validity/count/position, and geometry mode/value before printing the result.

Reproduction after uploading the current `k230/hcv/compat.py`, `detect_profile.py`, `geometry.py`, and `markers.py` to `/sdcard/hcv/hcv/`:

```text
python tools/board/board.py --port COM3 run tools/board/p2_t02_golden.py --timeout 45 --log reports/data/P2-T02/board-golden-6.4.1.log
```

Result: all seven cases passed. The raw output is in `board-golden-6.4.1.log`.
