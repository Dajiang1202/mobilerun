# OCR 服务生产部署 (V100 / Ubuntu 16.04)

> 把 dev 用的 RapidOCR(PP-OCRv4 ONNX) + FastAPI 服务部署到生产:
> **Tesla V100 + Ubuntu 16.04**, 与 bot 机同一网段。
> API 规格、字段见 [OCR服务部署需求.md](OCR服务部署需求.md); 实现代码 `services/ocr/ocr_server.py`。

---

## 一、目标 & 拓扑

- **硬件**: Tesla V100 (Volta, compute 7.0, 16/32GB)
- **系统**: Ubuntu 16.04 (2016, **glibc 2.23 偏老** —— 这是部署的主要坑)
- **网络**: 与 bot 机同一网段 (RTT 应 <30ms, 最好 <10ms)
- **引擎**: **必须和 dev 一致 = RapidOCR (ONNX Runtime, PP-OCRv4 mobile)**。
  - 别换成 PaddleOCR 或别的模型 —— ROI 阈值/纠错都是对着 RapidOCR 输出调的, 换了得重校。
- **客户端**: bot 机只需 `export OCR_URL=http://<v100-ip>:8089/ocr` (代码已支持 env 覆盖)。

---

## 二、为什么强烈推荐 Docker (别 native)

Ubuntu 16.04 的 glibc 2.23 太旧, 现代 `onnxruntime-gpu` wheel 要 glibc 2.27+,
native `pip install` 大概率报错 / CUDA 对不上。**Docker 用新 base 镜像隔离掉这层**,
host 只需 NVIDIA driver + nvidia-container-toolkit (这俩在 16.04 能装)。

---

## 三、前置 (host, 一次性)

```bash
# 1. NVIDIA driver (V100 装 525+ 驱动, 支持 CUDA 12)
nvidia-smi   # 能看到 V100 即可

# 2. nvidia-container-toolkit (16.04 需加 repo)
distribution=$(. /etc/os-release;echo $ID$VERSION_ID)
curl -s -L https://nvidia.github.io/libnvidia-container/gpgkey | sudo apt-key add -
curl -s -L https://nvidia.github.io/libnvidia-container/$distribution/libnvidia-container.list \
  | sudo tee /etc/apt/sources.list.d/nvidia-container-toolkit.list
sudo apt-get update && sudo apt-get install -y nvidia-container-toolkit
sudo systemctl restart docker

# 3. Docker
docker --version || sudo apt-get install -y docker.io
```

验证 GPU 在容器里能用:
```bash
docker run --rm --gpus all nvidia/cuda:11.8.0-base-ubuntu22.04 nvidia-smi
# 看到 V100 就 OK
```

---

## 四、部署文件 (拷到 V100 机器)

从本项目 `services/ocr/` 拷这两个到 V100 的 `/opt/ocr/`:
- `ocr_server.py` (FastAPI 服务, 已带 `/ocr` `/health`, 同步推理走 `asyncio.to_thread`)
- `gpu_config.yaml` (RapidOCR GPU 配置)

新建 `Dockerfile` 和 `requirements.txt`:

**`requirements.txt`**:
```
rapidocr-onnxruntime>=1.3.8
onnxruntime-gpu>=1.16,<1.18   # V100/CUDA11.8 兼容区间
fastapi>=0.100
uvicorn[standard]>=0.23
python-multipart
```

**`Dockerfile`** (CUDA 11.8 + cuDNN 8 —— V100 黄金组合, 稳):
```dockerfile
FROM nvidia/cuda:11.8.0-cudnn8-runtime-ubuntu22.04

# 基础 + Python 3.10 (新 glibc 在镜像里, 不依赖 host 16.04)
RUN apt-get update && apt-get install -y python3.10 python3-pip libgl1 libglib2.0-0 \
    && ln -s /usr/bin/python3.10 /usr/bin/python \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY ocr_server.py gpu_config.yaml ./

EXPOSE 8089
# uvicorn 多 worker (代码用了 to_thread, 每个 worker 内部并行; 4 worker 足够 9 并发)
CMD ["uvicorn", "ocr_server:app", "--host", "0.0.0.0", "--port", "8089", "--workers", "4"]
```

> 想用 gunicorn 包一层 (生产更皮实): `pip install gunicorn`, CMD 换成
> `gunicorn ocr_server:app -k uvicorn.workers.UvicornWorker -w 4 -b 0.0.0.0:8089`
> 两者皆可, 代码无需改 (to_thread 已在)。

---

## 五、构建 & 运行

```bash
cd /opt/ocr
docker build -t tft-ocr .

# 启动 (注意 --gpus all)
docker run -d --name tft-ocr --gpus all --restart unless-stopped \
  -p 8089:8089 tft-ocr

# 看日志 (首次会加载模型 + warmup, ~10s)
docker logs -f tft-ocr
```

验证:
```bash
curl http://localhost:8089/health
# 期望: {"status":"ok","gpu":"Tesla V100 ...","model":"PP-OCRv4 (ONNX)"}
```
> 若 health 里 gpu 显示 "CPU" 而非 V100 —— onnxruntime 没拿到 GPU, 检查 `--gpus all` 和驱动。

---

## 六、上线前必测 (3 项, 一次验完)

从 **bot 机** (不是 V100 本机) 跑, 模拟真实链路:

1. **RTT**: 发一个真实 crop 的 `/ocr`, 看端到端往返。
   - <30ms → 闭眼用 | 30-60ms → 可用 | >60ms → 考虑本地 CPU 兜底
2. **并发**: 9 个 crop 并发, 墙钟应 <100ms (to_thread 生效)。
3. **准确率对齐**: 同一批 shop crop, 对比 dev(RapidOCR CPU) 和 prod(V100 GPU) 的识别结果。
   字段一致才算过 (同引擎, 应基本一致; 差异大说明引擎/版本漂了)。

`services/ocr/benchmark_realistic.py` 可直接拿来跑基准。

---

## 七、客户端切换

bot 机无需改代码, 只设环境变量:
```bash
export OCR_URL=http://<v100-ip>:8089/ocr
```
`run_tft_replay.py` / `run_tft_device.py` / `pregame.py` 都读这个 env。

---

## 八、坑 & 排查

| 现象 | 原因 / 解决 |
|------|------------|
| `pip install onnxruntime-gpu` 报 glibc 错 | 别 native, 用 Docker (镜像内 glibc 新) |
| health 显示 CPU 不是 V100 | 容器没拿到 GPU: 检查 `--gpus all` + host `nvidia-container-toolkit` + `nvidia-smi` |
| onnxruntime 报 cuDNN/cuDso 找不到 | CUDA/cuDNN 版本不匹配。坚持用 `cuda11.8-cudnn8` 镜像, 别混装 |
| 延时 100ms+ 卡 | 同网段但走 VPN/跨机房 → 拉近或本地兜底; 或 bot 机 OCR 服务本地起一份 |
| 首次请求慢 | 模型 lazy-init, Dockerfile 加 warmup (代码 `_warmup` 已在 lifespan 跑) |
| 并发退化成串行 | worker 不够或没用 to_thread —— 代码已修; 确认 `-w 4` 且镜像里的 ocr_server.py 是新版 |

---

## 九、备选: native 部署 (不推荐, 仅记录)

若不能用 Docker:
1. 用 pyenv/deadsnakes 装 Python 3.10 (16.04 自带 3.5 太老)。
2. 装 CUDA 11.8 toolkit + cuDNN 8.9 (V100 驱动 525+)。
3. `pip install -r requirements.txt` —— glibc 报错概率高, 做好踩坑准备。
4. 跑 `uvicorn ocr_server:app --port 8089`。

不推荐, Docker 省太多事。
