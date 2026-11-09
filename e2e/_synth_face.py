"""Draws a simple, entirely synthetic cartoon face and writes it as a .y4m clip for Chromium's fake camera
(--use-file-for-fake-video-capture). No real person's image is involved. Needs Pillow, numpy and ffmpeg."""
import shutil, subprocess

def make(path, seconds=3, fps=10):
    if not shutil.which("ffmpeg"):
        return False
    try:
        from PIL import Image, ImageDraw, ImageFilter
        import numpy as np
    except ImportError:
        return False
    W, H, cx, cy = 640, 480, 320, 250
    img = Image.new("RGB", (W, H), (150, 160, 170)); d = ImageDraw.Draw(img)
    d.ellipse((cx - 230, 380, cx + 230, 700), fill=(50, 70, 110))
    d.rectangle((cx - 45, 330, cx + 45, 420), fill=(205, 160, 130))
    d.ellipse((cx - 125, 95, cx + 125, 360), fill=(40, 28, 22))
    d.ellipse((cx - 105, 115, cx + 105, 365), fill=(224, 172, 140))
    d.ellipse((cx - 122, 225, cx - 95, 290), fill=(214, 162, 130)); d.ellipse((cx + 95, 225, cx + 122, 290), fill=(214, 162, 130))
    d.pieslice((cx - 112, 100, cx + 112, 300), 180, 360, fill=(40, 28, 22))
    for s in (-1, 1):
        d.line((cx + s * 30, 205, cx + s * 72, 198), fill=(50, 35, 28), width=7)
        ex = cx + s * 50
        d.ellipse((ex - 24, 218, ex + 24, 246), fill=(245, 240, 235)); d.ellipse((ex - 11, 218, ex + 11, 246), fill=(70, 45, 30)); d.ellipse((ex - 5, 225, ex + 5, 238), fill=(10, 10, 10))
        d.arc((ex - 26, 212, ex + 26, 250), 200, 340, fill=(60, 40, 35), width=3)
    d.polygon([(cx, 225), (cx - 14, 290), (cx + 14, 290)], fill=(214, 160, 128)); d.ellipse((cx - 18, 282, cx + 18, 302), fill=(205, 150, 120))
    d.ellipse((cx - 9, 290, cx - 3, 298), fill=(120, 70, 60)); d.ellipse((cx + 3, 290, cx + 9, 298), fill=(120, 70, 60))
    d.ellipse((cx - 36, 320, cx + 36, 345), fill=(170, 70, 75)); d.line((cx - 36, 332, cx + 36, 332), fill=(110, 40, 45), width=3)
    a = np.asarray(img.filter(ImageFilter.GaussianBlur(1.2))).astype(np.int16)
    rng = np.random.default_rng(1)
    raw = b"".join(np.clip(a + rng.normal(0, 3, a.shape).astype(np.int16), 0, 255).astype(np.uint8).tobytes() for _ in range(seconds * fps))
    r = subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(fps), "-i", "-", "-pix_fmt", "yuv420p", path], input=raw)
    return r.returncode == 0
