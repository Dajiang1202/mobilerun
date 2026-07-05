"""Benchmark: base64 vs binary upload."""
import base64, cv2, numpy as np, json, urllib.request, time, io

# Create test image
img = np.zeros((80, 200, 3), dtype=np.uint8)
img[:] = (40, 20, 60)
cv2.putText(img, "42", (30, 55), cv2.FONT_HERSHEY_SIMPLEX, 2, (255, 255, 255), 3)
_, buf = cv2.imencode(".png", img)
img_bytes = buf.tobytes()
b64 = base64.b64encode(img_bytes).decode()

# Warmup
print("=== Warmup ===")
for i in range(5):
    data = json.dumps({"image": b64, "lang": "ch"}).encode()
    req = urllib.request.Request("http://localhost:8089/ocr", data=data, headers={"Content-Type": "application/json"})
    urllib.request.urlopen(req).read()

# Base64 benchmark
print("\n=== Base64 JSON (20 requests) ===")
latencies_b64 = []
for i in range(20):
    t0 = time.perf_counter()
    data = json.dumps({"image": b64, "lang": "ch"}).encode()
    req = urllib.request.Request("http://localhost:8089/ocr", data=data, headers={"Content-Type": "application/json"})
    resp = urllib.request.urlopen(req)
    result = json.loads(resp.read())
    latency = (time.perf_counter() - t0) * 1000
    latencies_b64.append(latency)

s = sorted(latencies_b64)
print("Avg: %.1fms  Min: %.1fms  Max: %.1fms  P50: %.1fms  P95: %.1fms" % (
    sum(s)/len(s), s[0], s[-1], s[len(s)//2], s[int(len(s)*0.95)]))

# Binary upload benchmark
print("\n=== Binary Upload (20 requests) ===")
latencies_bin = []
for i in range(20):
    t0 = time.perf_counter()
    boundary = "----boundary123"
    body = (
        "--%s\r\n"
        'Content-Disposition: form-data; name="image"; filename="test.png"\r\n'
        "Content-Type: image/png\r\n\r\n"
    ) % boundary
    body = body.encode() + img_bytes + ("\r\n--%s--\r\n" % boundary).encode()
    req = urllib.request.Request(
        "http://localhost:8089/ocr_binary",
        data=body,
        headers={"Content-Type": "multipart/form-data; boundary=%s" % boundary},
    )
    resp = urllib.request.urlopen(req)
    result = json.loads(resp.read())
    latency = (time.perf_counter() - t0) * 1000
    latencies_bin.append(latency)

s = sorted(latencies_bin)
print("Avg: %.1fms  Min: %.1fms  Max: %.1fms  P50: %.1fms  P95: %.1fms" % (
    sum(s)/len(s), s[0], s[-1], s[len(s)//2], s[int(len(s)*0.95)]))

print("\n=== Server-side latency (from responses) ===")
# Re-run with server latency
data = json.dumps({"image": b64}).encode()
req = urllib.request.Request("http://localhost:8089/ocr", data=data, headers={"Content-Type": "application/json"})
r = json.loads(urllib.request.urlopen(req).read())
print("Base64 server: %dms" % r["latency_ms"])
