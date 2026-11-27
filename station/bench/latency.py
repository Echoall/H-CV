"""Show a clock beside its video and measure the difference in paired screenshots."""

import os
from statistics import median
import time


SAMPLE_SECONDS = (10, 14, 18, 22, 26, 30, 34)
MIN_SAMPLES = 5
LIMIT_MS = 300


def result(readings):
    values = [sample["delay_ms"] for sample in readings]
    maximum = max(values) if values else None
    return {
        "readings": readings,
        "sample_count": len(values),
        "median_ms": median(values) if values else None,
        "max_ms": maximum,
        "limit_ms": LIMIT_MS,
        "passed": len(values) >= MIN_SAMPLES and maximum <= LIMIT_MS,
    }


def read_samples(directory, ask=input):
    images = sorted(directory.glob("latency-*.png"))
    if not images:
        print("本次没有保存延迟截图，延迟待测。")
        return result([])
    print(f"已保存 {len(images)} 张延迟截图。需要至少 5 张能看清两组数字的截图。")
    print("可以现在读数，也可以联网后请我查看本机截图。")
    if ask("输入 1 打开截图文件夹并填读数，直接回车则稍后分析 > ").strip() != "1":
        return result([])
    if os.name == "nt":
        os.startfile(directory)
    readings = []
    for image in images:
        while True:
            text = ask(f"{image.name}，依次输入左、右两个数字，用空格隔开。回车跳过 > ").strip()
            if not text:
                break
            numbers = text.split()
            if len(numbers) == 2 and all(item.isascii() and item.isdecimal()
                                         and len(item) <= 8 for item in numbers):
                left, right = map(int, numbers)
                if left >= right:
                    readings.append({"image": image.name, "left_ms": left, "right_ms": right,
                                     "delay_ms": left - right})
                    break
            print("请核对截图，左边时间应大于或等于右边，读不清可以跳过。")
    return result(readings)


class ClockWindow:
    def __init__(self, directory):
        import tkinter as tk
        from PIL import ImageGrab

        self.root = tk.Tk()
        self.root.title("图传延迟测量")
        self.root.configure(background="#111827")
        self.root.geometry("+0+0")
        self.root.attributes("-fullscreen", True)
        self.root.protocol("WM_DELETE_WINDOW", self.cancel)
        self.root.bind("<Escape>", lambda _: self.cancel())
        self.cancelled = False
        self.directory = directory
        self.grab = ImageGrab.grab
        self.count = 0
        self.origin = time.monotonic_ns()
        self.root.grid_columnconfigure((0, 1), weight=1, uniform="panels")
        self.root.grid_rowconfigure(1, weight=1)
        widths = self.root.winfo_screenwidth() // 2
        self.title = tk.Label(self.root, text="前 10 秒请把镜头对准左边数字，随后自动截图。Esc 可以结束。",
                              foreground="#ffffff", background="#111827",
                              font=("Arial", 18), pady=16)
        self.title.grid(row=0, column=0, columnspan=2, sticky="ew")
        self.clock = tk.Label(self.root, text="00000000", foreground="#111827", background="#ffffff",
                              font=("Courier Prime", -max(36, widths // 7), "bold"))
        self.clock.grid(row=1, column=0, sticky="nsew")
        video = tk.Frame(self.root, background="#000000")
        video.grid(row=1, column=1, sticky="nsew")
        for i, label in enumerate(("电脑上的时钟（毫秒）", "板子传回的画面")):
            tk.Label(self.root, text=label, foreground="#ffffff", background="#111827",
                     font=("Arial", 17), pady=13).grid(row=2, column=i, sticky="ew")
        self.root.update()
        self.hwnd = video.winfo_id()

    def cancel(self):
        self.cancelled = True

    def tick(self, elapsed=0):
        self.clock.configure(text=f"{(time.monotonic_ns() - self.origin) // 1_000_000:08d}")
        self.root.update()
        if self.cancelled:
            raise RuntimeError("已按 Esc 结束延迟测量。")
        if self.count < len(SAMPLE_SECONDS) and elapsed >= SAMPLE_SECONDS[self.count]:
            self.count += 1
            self.title.configure(text=f"已到第 {self.count} 张截图，请保持相机位置。Esc 可以结束。")
            self.root.update_idletasks()
            path = self.directory / f"latency-{self.count:02d}.png"
            self.grab().save(path)

    def close(self):
        self.root.destroy()
