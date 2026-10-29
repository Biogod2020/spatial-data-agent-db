# SpatialDataAgent-DB 用户工具

这个仓库帮助用户从数据库中**找到样本、按需下载，并正确读取表达、空间坐标和图像**。
用户可以独立安装，无需运行主项目的数据发现与标准化管线。

[English](README.md) · [字段说明](docs/data-dictionary.md) · [Python 接口](docs/python-api.md) ·
[数据库](https://huggingface.co/datasets/Biogod/SpatialDataAgent-DB)

## 安装与访问

需要 Python 3.11 或更新版本：

```bash
git clone https://github.com/Biogod2020/spatial-data-agent-db.git
cd spatial-data-agent-db
python -m pip install -e .
hf auth login
```

如数据库要求申请访问，请先在 Hugging Face 数据库页面用自己的账户接受访问条件。
认证沿用本机 Hugging Face 登录或 `HF_TOKEN`；请勿把凭据放入代码或命令参数。

## 1. 查找需要的样本

```bash
sda-db search --where 'organism=Homo sapiens' --where 'platform=Visium' \
  --where 'has_image=true' --where 'split=train' --output selection.json
sda-db search --text brain --min-obs 1000 --limit 5
```

检索只下载目录。`--where` 按字段精确匹配、不区分大小写，多项条件取交集；
`--text` 在目录字段中匹配字面子串，`--min-obs` / `--max-obs` 包含边界。
GSE 条件匹配样本所属的任一系列。这里不调用模型，也不执行主项目的意图检索实验。
`selection.json` 保存全部匹配行、数据库提交号及目录哈希，显示行数由 `--limit` 单独控制。

## 2. 下载与校验

```bash
sda-db download GSM3036911 --destination downloads
```

用检索结果中的 GSM 替换示例，可一次传入多个 GSM。默认下载表达与坐标的 `spatial.h5ad`，
以及元数据 `sample.json`；加 `--include-image` 后才下载已发布的 `image.tif`。
文件按数据库 commit 分目录存放，下载完成检查 SHA256，已有且校验通过的文件直接复用。
若本地同名文件字节不同，命令报错并保留原文件，请选择新的下载目录。

```bash
sda-db verify downloads/8f116b867ce4097957d4209db712d37bae3c9369/samples/GSE111672/GSM3036911
```

校验命令不联网；下载回执位于 `downloads/<commit>/.sda/receipts/<GSM>.json`。
它核对文件字节和回执，不重新执行发布者的科学评分与人工审核。

## 3. 在 Python 中使用

```python
from sda_db import Catalog, download_sample

catalog = Catalog.from_hub()
selected = catalog.search(organism="Homo sapiens", platform="Visium", split="train")
selected.save_selection("selection.json")
for row in selected.rows[:2]:  # 确认规模后再选择下载数量
    print(row["gsm"], row["tissue"], row["n_obs"])
    directory = download_sample(selected, row["gsm"], "downloads")
```

安装读取依赖：`python -m pip install -e '.[analysis]'`。

```python
from sda_db.io import read_sample, coordinates

adata, meta = read_sample(directory)  # 默认 backed='r'，避免载入整个表达矩阵
print(adata.shape, meta["expression"]["state"])
xy = coordinates(adata, meta)         # 原始坐标；单位见 meta['coordinates']['units']
adata.file.close()
```

完整示例见 [examples](examples/)；图像坐标转换、降采样层级与特征处理见
[字段说明](docs/data-dictionary.md)。

## 4. 逐样本处理与受控缓存

SDK 0.2 支持一边分析当前样本，一边预取后续样本；缓存按最近使用顺序回收已释放的数据。

```python
from sda_db import stream_samples
from sda_db.io import read_sample

with stream_samples(selected, cache_dir=".sda-cache", max_cache_bytes=10 * 1024**3,
                    prefetch=1) as samples:
    for sample in samples:
        adata, meta = read_sample(sample.directory, verify=False)  # 交付前已经校验
        try:
            print(sample.gsm, adata.shape)
        finally:
            adata.file.close()
```

下载前先读取同一固定版本的文件大小并预留容量；容量不够时，预取等待当前样本释放。
**进入下一次迭代前关闭 AnnData/TIFF 文件**：上一样本的缓存随后可能被删除。
也可调用 `sample.release()` 提前释放。单个样本必须能放入指定预算，否则下载前报错。
`max_cache_bytes` 限制样本文件及下载预留字节，另有少量回执、Hub 元数据和文件系统开销。

请用专门的空目录作为缓存；同一目录同一时间由一个流式迭代器独占。
缓存使用直接落盘模式，避免大文件在 Hub 缓存和目标目录重复存放。
永久保存时可用 `download_sample(..., storage="direct")` 或
`sda-db download GSM3036911 --storage direct`，仍保留完整哈希校验。
更多选项、离线复用及提前退出行为见 [streaming.md](docs/streaming.md)，
可运行示例见 [stream_samples.py](examples/stream_samples.py)。

## 版本与使用要点

首版客户端默认固定到已有发布提交 `8f116b867ce4097957d4209db712d37bae3c9369`。
`sda-db search --revision main` 或 `Catalog.from_hub(revision="main")` 可主动查看最新目录；
客户端先将分支解析为准确提交，后续下载沿用同一提交。复现时保存并使用完整 commit。
`--offline` 只读已有 Hub 缓存，需要完整 commit；本地 CSV 可用 `--catalog-csv` 读取。

- 表达矩阵是否为原始计数，应结合 `expression_state` 和 `verification` 判断。
- `spatial` 可能是像素或网格坐标；请求 `units="um"` 只返回已记录的 µm 坐标，无依据时明确报错。
- 图像并非每个样本都有。坐标投影使用 `image.spatial_to_pixel`，对应原始图像第 0 层。
- 保留原有 `split`、GSE 归属、`content_group` 和 `aliases`，避免训练/测试数据泄漏。
- `drop_features` 是使用前应移除的特征；QC 标注与人审状态应按研究目标显式筛选。
- 三类验证各自保留：结构评分、模态验收、人工视觉审核。空值不能当作 PASS。

数据复用与来源引用见 [reuse.md](docs/reuse.md)，访问问题见 [access.md](docs/access.md)。
