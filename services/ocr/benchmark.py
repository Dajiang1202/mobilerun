"""Quick OCR latency benchmark."""
import base64, cv2, numpy as np, json, urllib.request, time

img = np.zeros((80, 200, 3), dtype=np.uint8)
img[:] = (40, 20, 60)
cv2.putText(img, "42", (30, 55), cv2.FONT_HERSHEY_SIMPLEX, 2, (255, 255, 255), 3)
_, buf = cv2.imencode(".png", img)
b64 = base64.b64encode(buf).decode()
data = json.dumps({"image": b64, "lang": "ch"}).encode()

latencies = []
for i in range(10):
    t0 = time.perf_counter()
    req = urllib.request.Request(
        "http://localhost:8089/ocr",
        data=data,
        headers={"Content-Type": "application/json"},
    )
    resp = urllib.request.urlopen(req)
    result = json.loads(resp.read())
    latency = (time.perf_counter() - t0) * 1000
    latencies.append(latency)
    srv_ms = result["latency_ms"]
    texts = result["texts"]
    print("Request %d: %.1fms (server: %dms) texts=%s" % (i + 1, latency, srv_ms, texts))

print("---")
print("Avg: %.1fms" % (sum(latencies) / len(latencies)))
print("Min: %.1fms" % min(latencies))
print("Max: %.1fms" % max(latencies))
s = sorted(latencies)
print("P95: %.1fms" % s[int(len(s) * 0.95)])

# 10 并发测试
print("\n--- 10 concurrent ---")
import concurrent.futures

def single_req(_):
    t0 = time.perf_counter()
    req = urllib.request.Request(
        "http://localhost:8089/ocr",
        data=data,
        headers={"Content-Type": "application/json"},
    )
    resp = urllib.request.urlopen(req)
    result = json.loads(resp.read())
    return (time.perf_counter() - t0) * 1000

t0 = time.perf_counter()
with concurrent.futures.ThreadPoolExecutor(max_workers=10) as ex:
    results = list(ex.map(single_req, range(10)))
total = (time.perf_counter() - t0) * 1000
print("Total wall time: %.1fms" % total)
print("Per-request avg: %.1fms" % (sum(results) / len(results)))
print("Per-request max: %.1fms" % max(results))
