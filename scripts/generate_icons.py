#!/usr/bin/env python3
"""Generate crisp native app icons for macOS, Windows, Linux, Android, iPhone, and iPad."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
DESKTOP_BUILD = ROOT / "apps" / "desktop" / "build"
MOBILE_RES = ROOT / "apps" / "mobile" / "resources"


def draw_bruno_icon(size: int = 1024) -> Image.Image:
    """Render supersampled 2x Bruno AI emerald mascot icon and downsample cleanly."""
    s = size * 2
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))

    # Diagonal emerald gradient (#10b981 -> #047857)
    grad = Image.new("RGBA", (s, s), (0, 0, 0, 255))
    px = grad.load()
    for y in range(s):
        t_y = y / (s - 1)
        for x in range(s):
            t = 0.5 * (x / (s - 1)) + 0.5 * t_y
            r = int(16 * (1 - t) + 4 * t)
            g = int(185 * (1 - t) + 120 * t)
            b = int(129 * (1 - t) + 87 * t)
            px[x, y] = (r, g, b, 255)

    # Rounded square mask
    mask = Image.new("L", (s, s), 0)
    mdraw = ImageDraw.Draw(mask)
    radius = int(s * 0.22)
    mdraw.rounded_rectangle([0, 0, s - 1, s - 1], radius=radius, fill=255)
    img.paste(grad, (0, 0), mask)

    draw = ImageDraw.Draw(img)
    mint = (236, 253, 245, 245)
    dark_green = (6, 95, 70, 255)

    # Soft head circle
    cx, cy = s // 2, int(s * 0.455)
    hr = int(s * 0.22)
    draw.ellipse([cx - hr, cy - hr, cx + hr, cy + hr], fill=mint)

    # Eyes
    eye_r = int(s * 0.035)
    eye_dx = int(s * 0.078)
    eye_y = int(s * 0.422)
    for sign in (-1, 1):
        ex = cx + sign * eye_dx
        draw.ellipse([ex - eye_r, eye_y - eye_r, ex + eye_r, eye_y + eye_r], fill=dark_green)

    # Smile arc
    smile_w = int(s * 0.115)
    smile_top = int(s * 0.46)
    smile_bot = int(s * 0.56)
    draw.arc(
        [cx - smile_w, smile_top, cx + smile_w, smile_bot],
        start=15,
        end=165,
        fill=dark_green,
        width=max(4, int(s * 0.032)),
    )

    # Shoulder arc
    sh_w = int(s * 0.27)
    sh_top = int(s * 0.67)
    sh_bot = int(s * 0.96)
    draw.arc(
        [cx - sh_w, sh_top, cx + sh_w, sh_bot],
        start=205,
        end=335,
        fill=mint,
        width=max(6, int(s * 0.062)),
    )

    return img.resize((size, size), Image.Resampling.LANCZOS)


def build_icns(base_png: Path, out_icns: Path) -> None:
    """Create macOS .icns using iconutil."""
    iconset = out_icns.parent / "icon.iconset"
    if iconset.exists():
        shutil.rmtree(iconset)
    iconset.mkdir(parents=True, exist_ok=True)

    src = Image.open(base_png)
    specs = [
        ("icon_16x16.png", 16),
        ("icon_16x16@2x.png", 32),
        ("icon_32x32.png", 32),
        ("icon_32x32@2x.png", 64),
        ("icon_128x128.png", 128),
        ("icon_128x128@2x.png", 256),
        ("icon_256x256.png", 256),
        ("icon_256x256@2x.png", 512),
        ("icon_512x512.png", 512),
        ("icon_512x512@2x.png", 1024),
    ]
    for name, px in specs:
        src.resize((px, px), Image.Resampling.LANCZOS).save(iconset / name, "PNG")

    subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o", str(out_icns)], check=True)
    shutil.rmtree(iconset)


def main() -> None:
    DESKTOP_BUILD.mkdir(parents=True, exist_ok=True)
    MOBILE_RES.mkdir(parents=True, exist_ok=True)

    master = draw_bruno_icon(1024)
    desktop_png = DESKTOP_BUILD / "icon.png"
    mobile_png = MOBILE_RES / "icon.png"
    tray_png = DESKTOP_BUILD / "tray.png"
    ico_path = DESKTOP_BUILD / "icon.ico"
    icns_path = DESKTOP_BUILD / "icon.icns"

    master.save(desktop_png, "PNG")
    master.save(mobile_png, "PNG")
    master.resize((32, 32), Image.Resampling.LANCZOS).save(tray_png, "PNG")
    master.save(
        ico_path,
        "ICO",
        sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)],
    )
    if shutil.which("iconutil"):
        build_icns(desktop_png, icns_path)

    # Populate Android mipmaps if Capacitor Android project exists
    android_res = ROOT / "apps" / "mobile" / "android" / "app" / "src" / "main" / "res"
    if android_res.is_dir():
        for mipmap_dir in android_res.glob("mipmap-*"):
            for target_png in mipmap_dir.glob("*.png"):
                try:
                    w, h = Image.open(target_png).size
                    master.resize((w, h), Image.Resampling.LANCZOS).save(target_png, "PNG")
                except Exception:
                    pass
        print(f"Updated Android mipmap icons under {android_res.relative_to(ROOT)}")

    # Populate iOS / iPadOS AppIcon if Capacitor iOS project exists
    ios_icon = (
        ROOT
        / "apps"
        / "mobile"
        / "ios"
        / "App"
        / "App"
        / "Assets.xcassets"
        / "AppIcon.appiconset"
        / "AppIcon-512@2x.png"
    )
    if ios_icon.parent.is_dir():
        # iOS icons require opaque RGB PNG (no alpha channel)
        ios_rgb = Image.new("RGB", master.size, (16, 185, 129))
        ios_rgb.paste(master, (0, 0), master)
        ios_rgb.save(ios_icon, "PNG")
        print(f"Updated {ios_icon.relative_to(ROOT)} ({ios_icon.stat().st_size} bytes)")

    for p in (desktop_png, tray_png, ico_path, icns_path, mobile_png):
        if p.exists():
            print(f"Generated {p.relative_to(ROOT)} ({p.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
