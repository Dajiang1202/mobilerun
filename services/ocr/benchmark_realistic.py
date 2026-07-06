"""模拟游戏帧 OCR 场景 — 10 个不同大小的裁剪区域"""
import base64, cv2, numpy as np, json, urllib.request, time, concurrent.futures

# 模拟游戏中的 OCR 区域
REGIONS = {
    "gold":        (60, 100),    # 金币数字
    "level":       (40, 50),     # 等级
    "hp":          (90, 50),     # 血量
    "timer":       (90, 60),     # 倒计时
    "shop_slot_0": (120, 250),   # 商店棋子1
    "shop_slot_1": (120, 250),   # 商店棋子2
    "shop_slot_2": (120, 250),   # 商店棋子3
    "shop_slot_3": (120, 250),   # 商店棋子4
    "shop_slot_4": (120, 250),   # 商店棋子5
    "bench":       (660, 100),   # 备战席
}

def make_test_image(h, w, text="test"):
    img = np.zeros((h, w, 3), dtype=np.uint8)
    img[:] = (40, 20, 60)
    font_scale = min(h, w) / 80
    cv2.putText(img, text, (5, int(h*0.7)), cv2.FONT_HERSHEY_SIMPLEX, font_scale, (255, 255, 255), max(1, int(font_scale*1.5)))
    _, buf = cv2.imencode(".png", img)
    return base64.b64encode(buf).decode()

def send_ocr(name, b64):
    data = json.dumps({"image": b64, "lang": "ch"}).encode()
    t0 = time.perf_counter()
    req = urllib.request.Request("http://localhost:8089/ocr", data=data, headers={"Content-Type": "application/json"})
    resp = urllib.request.urlopen(req)
    result = json.loads(resp.read())
    latency = (time.perf_counter() - t0) * 1000
    return name, latency, result["latency_ms"]

# 预热 (3帧)
print("=== Warming up (3 frames) ===")
for i in range(3):
    imgs = {k: make_test_image(h, w, k) for k, (h, w) in REGIONS.items()}
    t0 = time.perf_counter()
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as ex:
        futures = [ex.submit(send_ocr, k, v) for k, v in imgs.items()]
        for f in concurrent.futures.as_completed(futures):
            f.result()
    print("  Frame %d: %.0fms total" % (i+1, (time.perf_counter()-t0)*1000))

# 正式测试 (5帧)
print("\n=== Benchmark (5 frames, 10 regions each) ===")
frame_times = []
all_latencies = []
for frame in range(5):
    imgs = {k: make_test_image(h, w, k) for k, (h, w) in REGIONS.items()}
    t0 = time.perf_counter()
    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as ex:
        futures = [ex.submit(send_ocr, k, v) for k, v in imgs.items()]
        for f in concurrent.futures.as_completed(futures):
            results.append(f.result())
    frame_ms = (time.perf_counter()-t0)*1000
    frame_times.append(frame_ms)

    results.sort(key=lambda x: x[1])
    for name, latency, srv_ms in results:
        all_latencies.append(latency)
        print("  [%s] %s: %.1fms (server: %dms)" % (frame+1, name, latency, srv_ms))
    print("  Frame %d total: %.0fms" % (frame+1, frame_ms))

print("\n=== Summary ===")
print("Frame total avg: %.0fms" % (sum(frame_times)/len(frame_times)))
print("Frame total min: %.0fms" % min(frame_times))
print("Frame total max: %.0fms" % max(frame_times))
s = sorted(all_latencies)
print("Per-request avg: %.1fms" % (sum(s)/len(s)))
print("Per-request p50: %.1fms" % s[len(s)//2])
print("Per-request p95: %.1fms" % s[int(len(s)*0.95)])
print("Per-request max: %.1fms" % max(s))
