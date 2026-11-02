# SpatialDataAgent-DB 工具包

本工具包用于查找、下载、校验、读取和引用 [SpatialDataAgent-DB](https://huggingface.co/datasets/Biogod/SpatialDataAgent-DB)
中的样本。SpatialDataAgent-DB 是一个带版本的公共空间转录组图谱。论文版本收录 **1,265 个 GEO 研究的 12,163 个样本**，
涵盖 12 类平台（Visium、Visium HD、Xenium、CosMx、MERFISH/MERSCOPE、Slide-seq、Stereo-seq、legacy ST 等）。
其中 **8,211 个样本配有已配准的组织图像**。每个样本都有经过校验的表达矩阵、坐标和发布元数据。

同一个包服务三类使用者：

- **研究者**：Python API，以及在终端输出易读表格的 CLI。
- **脚本**：JSON 输出、稳定的错误码、可复现的样本选择记录。
- **AI 智能体**：MCP 服务器、精简 JSON 和智能体技能文件，可以安全地检索、规划下载并读取样本。

**检索不需要账号。** 论文版本的目录在公开仓库中有镜像，客户端用 SHA256 把镜像文件绑定到该版本。
下载样本文件需要获批的访问权限（见[访问权限](#访问权限)）。

[English](README.md) · [命令行](docs/cli.md) · [Python API](docs/python-api.md) · [AI 智能体与 MCP](docs/agents.md) ·
[用例](docs/cookbook.md) · [数据字典](docs/data-dictionary.md) · [流式读取](docs/streaming.md) · [验证记录](docs/validation.md)

## 安装

需要 Python 3.11 及以上：

```bash
pip install "spatial-data-agent-db[analysis] @ git+https://github.com/Biogod2020/spatial-data-agent-db"
```

`analysis` 附加项提供 AnnData、NumPy、pandas 和 tifffile，用于读取样本。只做检索和下载时，核心安装就够了。
也可以不安装，直接运行：

```bash
uvx --from "git+https://github.com/Biogod2020/spatial-data-agent-db" sda-db search alzheimer
```

## 一分钟上手命令行

```bash
sda-db search alzheimer hippocampus --organism human        # 排序检索；不下载，不需登录
sda-db values tissue --platform Xenium --top 15            # 先看某个字段有哪些取值
sda-db show GSM8185219                                     # 单个样本的全部信息
sda-db download GSM8185219 --dry-run                       # 传输前先看大小
sda-db download GSM8185219                                 # 按 SHA256 校验，并生成下载凭据
sda-db inspect GSM8185219                                  # 形状、坐标、质控、图像层级
sda-db search --platform "visium hd" --has-image --output cohort.json
sda-db download --from cohort.json --include-image --max-size 100GB
sda-db cite --from cohort.json -o refs.bib                 # 数据库及全部原始研究的引用
sda-db doctor                                              # 登录、权限、磁盘、网络诊断
```

在终端中输出表格；输出被管道或文件接收时，输出精简 JSON（`--json` 可强制 JSON）。
命令之间可以通过样本编号串联：

```bash
sda-db search lung --platform Xenium --format ids | sda-db download --from - --dry-run
```

## 一分钟上手 Python

```python
import sda_db

catalog = sda_db.open_catalog()                  # 论文版本 v1.0.0-20261025
hits = catalog.search("alzheimer hippocampus", organism="human", has_image=True)
hits.to_pandas()[["gsm", "tissue", "disease", "n_obs", "score"]]
hits.list_studies()                              # 每个 GEO 研究一行，含标题和 PubMed ID
print(hits.plan_download(include_image=True).to_dict(per_sample=False))   # 先看大小
report = hits.download("downloads", include_image=True, max_bytes="50GB")
adata = sda_db.load("GSM8185219")                # 下载（已校验）并读成 AnnData
adata.uns["sda_db"]                              # 版本、提交、单位、染色、完整 sample.json
```

要为图像–表达模型切图，可用 `sda_db.image.read_patch` 配合 `sda_db.io.image_coordinates`。
需要在磁盘预算内处理大量样本时，用 `catalog.stream(...)`。详见 [用例](docs/cookbook.md)。

## AI 智能体

注册一次 MCP 服务器，智能体即可检索、查看、规划、下载和引用样本：

```bash
claude mcp add sda-db -- sda-db mcp                       # Claude Code
```

服务器不依赖额外的包，提供 12 个工具：`search_samples`、`field_values`、`list_fields`、`summarize`、`get_sample`、
`get_study`、`plan_download`、`download_samples`、`inspect_sample`、`cite`、`list_releases` 和 `check_access`。

下载遵循“先规划”的原则：

- 必须显式传入 `confirm=true` 才会开始传输。
- 超过用户设定的大小上限会被拒绝。
- 文件只写入用户指定的目录。

Claude Desktop、Codex、Cursor、Gemini CLI 和 VS Code 的配置方法见 [AI 智能体与 MCP](docs/agents.md)。
不使用 MCP 的智能体可以调用 JSON 命令行，并阅读[智能体技能文件](skills/sda-db/SKILL.md)。

## 检索语法

| 写法 | 含义 |
|---|---|
| `alzheimer hippocampus` | 关键词排序检索（BM25），范围包括组织、疾病、物种、平台、编号以及研究的标题、设计和摘要；所有词都必须命中 |
| `"spinal cord"` · `-mouse` · `neuro*` | 短语 · 排除 · 前缀 |
| `--platform "visium hd"` · `--organism human` | 精确值；忽略大小写、空格和连字符，接受常用名；重复即为“或” |
| `--tissue cortex` · `--disease glioma` | GEO 原文中的子串，不区分大小写 |
| `--where "n_obs>=50000"` · `"split=train\|val"` · `"disease="` | 任意字段：`=`、`!=`、`~`（包含）、`!~`、`>`、`>=`、`<`、`<=`；`\|` 表示“或”；值为空表示缺失 |

拼错字段名或受控取值时会报错，并给出最接近的候选值（如 `platform='Visum'` → `'Visium', 'Visium HD'`）。
没有命中任何样本的检索词会在结果中注明，不会被悄悄丢弃。检索是确定性的：首次下载目录后可离线运行，不调用任何模型。

## 版本与可复现性

| 版本 | 提交 | 样本数 | 说明 |
|---|---|---:|---|
| `v1.0.0-20261025`（默认；别名 `paper`、`v1`） | `999566b` | 12,163 | 论文版本；目录公开，并以 SHA256 绑定 |
| `latest` | 打开时解析 | 持续增长 | 每日新增经审核样本的滚动版本；需要访问权限 |

每个样本选择、下载记录和引用都会写明版本名和完整提交号。`Catalog.from_selection("cohort.json")`
会按原提交重新打开目录，并选出同一批样本。

## 访问权限

样本文件需要人工审核，并要求 Hugging Face 账号中有已验证的机构邮箱：

1. 在[数据集页面](https://huggingface.co/datasets/Biogod/SpatialDataAgent-DB)提交申请。
2. 获批后运行 `hf auth login`，或设置 `HF_TOKEN`。
3. 运行 `sda-db doctor` 确认可以下载。

不要把 token 写进脚本或命令参数。详见[访问与排错](docs/access.md)。

## 正确理解数据

- **表达值**：`X` 不一定都是原始计数，请查看 `expression_state` 和 `verification`。
- **坐标单位**：`obsm['spatial']` 保留原始单位（um / pixel / native_grid / unstated）。只有记录了微米信息时才有
  微米坐标，其依据见 `um_basis`。
- **观测单位**：观测可能是 spot、bin、cell 或 bead，不都是细胞。
- **图像**：图像是可选项，需要显式下载。`spatial_to_pixel` 把原始 x/y 映射到第 0 层像素；带“(heuristic)”的染色标签是根据像素推断的。
- **机器学习**：保留发布版本的 `split`，同一研究的样本放在一起，并注意 `content_group` 和 `aliases`。
- **证据字段**：结构校验、图像配准和人工审核是三个独立字段；`review_decision` 为空不等于审核通过。

## 引用

`sda-db cite` 会为所选样本生成 BibTeX，内容包括数据库论文、所用版本以及每个 GEO 研究。请同时引用原始研究。

> Ji J-H, Zou Q, Cheng J, et al. *An agent-built, verified atlas of public spatial transcriptomics shows
> that breadth across studies drives transfer and reproducibility.* bioRxiv (2026).
> [doi:10.64898/2026.05.27.727615](https://doi.org/10.64898/2026.05.27.727615)

## 开发

```bash
python -m pip install -e '.[analysis]'
python -m unittest discover -s tests -v
```

测试全部离线进行，使用合成数据。在 Hub 上实际运行的检查记录在[验证记录](docs/validation.md)中。
