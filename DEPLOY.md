# 部署指南

## 环境依赖

- Python 3.10+
- HarmonyOS 设备 + HDC 工具
- `pip install jpype1 av opencv-python numpy pillow httpx openai py7zr`

## 大型资源文件

以下文件因体积较大，需手动下载放入 `D:\resource\` 目录：

```
D:\resource\
├── jbr\                          # JetBrains Runtime (~478MB)
│   └── bin\server\jvm.dll
├── pikafish\                     # 皮卡鱼象棋引擎 (~53MB)
│   └── Windows\
│       ├── pikafish-bmi2.exe     # 引擎主程序 (~1.5MB)
│       └── pikafish.nnue         # 神经网络权重 (~51MB)
└── hosScrcpy-1.0.15-beta.jar     # HOScrcpy SDK (~200KB)
```

### 下载链接

| 文件 | 来源 | 大小 |
|------|------|------|
| JDK/JBR | [Adoptium JRE 17](https://adoptium.net/download/) 或 DevEco Studio 自带 | ~200-478MB |
| Pikafish | [GitHub Releases](https://github.com/official-pikafish/Pikafish/releases) | ~65MB |
| HOScrcpy JAR | 项目自带 `gameauto/resource/` 或 [HOScrcpy](https://gitcode.com/OpenHarmonyToolkitsPlaza/HOScrcpy) | ~200KB |

> 网盘备用链接：待补充

## 快速启动

```bash
# 斗地主
python gameauto/run_doudizhu.py

# 天天象棋
python gameauto/run_xiangqi.py
```
