# 文件路径使用示例

> **最后更新**: 2026-04-10

## 多租户路径体系

DeerFlow 实现了三层租户隔离路径体系（tenant → user → thread），所有文件操作都在此体系下进行。

### 物理路径结构

```
backend/.deer-flow/
├── tenants/{tenant_id}/
│   ├── agents/                    # 租户级别智能体配置
│   ├── skills/                    # 租户级别技能
│   ├── memory.json                # 租户级别记忆
│   └── users/{user_id}/
│       ├── agents/                # 用户个人智能体配置
│       ├── skills/                # 用户个人技能
│       ├── memory.json            # 用户个人记忆
│       └── threads/{thread_id}/
│           └── user-data/
│               ├── workspace/     # 工作目录
│               ├── uploads/       # 上传文件
│               └── outputs/       # 输出文件
└── sandbox_state/{thread_id}/
    └── sandbox.json               # 沙箱状态（独立于用户数据）
```

## 三种路径类型

DeerFlow 的文件上传系统返回三种不同的路径，每种路径用于不同的场景：

### 1. 实际文件系统路径 (path)

```
.deer-flow/tenants/{tenant_id}/users/{user_id}/threads/{thread_id}/user-data/uploads/document.pdf
```

**用途：**
- 文件在服务器文件系统中的实际位置
- 相对于 `backend/` 目录
- 用于直接文件系统访问、备份、调试等
- 路径中包含 tenant_id 和 user_id，确保多租户隔离

**示例：**
```python
# Python 代码中通过 Paths 单例访问
from src.config.paths import Paths

paths = Paths.get()
thread_dir = paths.tenant_user_thread_dir(tenant_id, user_id, thread_id)
uploads_dir = thread_dir / "user-data" / "uploads"
file_path = uploads_dir / "document.pdf"
content = file_path.read_bytes()
```

### 2. 虚拟路径 (virtual_path)

```
/mnt/user-data/uploads/document.pdf
```

**用途：**
- Agent 在沙箱环境中使用的路径
- 沙箱系统会自动映射到对应租户/用户的实际路径
- Agent 的所有文件操作工具都使用这个路径
- Agent 无需感知多租户隔离细节

**示例：**
Agent 在对话中使用：
```python
# Agent 使用 read_file 工具
read_file(path="/mnt/user-data/uploads/document.pdf")

# Agent 使用 bash 工具
bash(command="cat /mnt/user-data/uploads/document.pdf")
```

### 3. HTTP 访问 URL (artifact_url)

```
/api/threads/{thread_id}/artifacts/mnt/user-data/uploads/document.pdf
```

**用途：**
- 前端通过 HTTP 访问文件
- 用于下载、预览文件
- 可以直接在浏览器中打开

**示例：**
```typescript
const threadId = 'abc123';
const filename = 'document.pdf';

// 下载文件
const downloadUrl = `/api/threads/${threadId}/artifacts/mnt/user-data/uploads/${filename}?download=true`;
window.open(downloadUrl);

// 在新窗口预览
const viewUrl = `/api/threads/${threadId}/artifacts/mnt/user-data/uploads/${filename}`;
window.open(viewUrl, '_blank');

// 使用 fetch API 获取
const response = await fetch(viewUrl);
const blob = await response.blob();
```

## 路径转换表

| 场景 | 使用的路径类型 | 示例 |
|------|---------------|------|
| 服务器后端代码直接访问 | `path` | `.deer-flow/tenants/acme/users/u1/threads/t1/user-data/uploads/file.pdf` |
| Agent 工具调用 | `virtual_path` | `/mnt/user-data/uploads/file.pdf` |
| 前端下载/预览 | `artifact_url` | `/api/threads/t1/artifacts/mnt/user-data/uploads/file.pdf` |
| 备份脚本 | `path` | （同实际路径） |
| 日志记录 | `path` | （同实际路径） |

## 虚拟路径 ↔ 物理路径 映射

| 虚拟路径 | 物理路径 |
|---------|---------|
| `/mnt/user-data/workspace` | `backend/.deer-flow/tenants/{tid}/users/{uid}/threads/{thid}/user-data/workspace` |
| `/mnt/user-data/uploads` | `backend/.deer-flow/tenants/{tid}/users/{uid}/threads/{thid}/user-data/uploads` |
| `/mnt/user-data/outputs` | `backend/.deer-flow/tenants/{tid}/users/{uid}/threads/{thid}/user-data/outputs` |
| `/mnt/skills` | `deer-flow/skills/` |

路径映射通过 `replace_virtual_path()` / `replace_virtual_paths_in_command()` 实现，带有路径遍历防护。

## 完整使用流程示例

### 场景：前端上传文件并让 Agent 处理

```typescript
async function uploadAndProcess(threadId: string, file: File) {
  // 1. 上传文件
  const formData = new FormData();
  formData.append('files', file);

  const uploadResponse = await fetch(
    `/api/threads/${threadId}/uploads`,
    { method: 'POST', body: formData }
  );

  const uploadData = await uploadResponse.json();
  const fileInfo = uploadData.files[0];
  // fileInfo 包含: filename, path, virtual_path, artifact_url,
  //                markdown_file, markdown_path, markdown_virtual_path, markdown_artifact_url

  // 2. 发送消息给 Agent
  await sendMessage(threadId, "请分析刚上传的 PDF 文件");
  // Agent 会自动看到文件列表（通过 UploadsMiddleware 注入）

  // 3. 访问转换后的 Markdown
  const mdResponse = await fetch(fileInfo.markdown_artifact_url);
  const markdownContent = await mdResponse.text();

  // 4. 下载原始 PDF
  const downloadLink = document.createElement('a');
  downloadLink.href = fileInfo.artifact_url + '?download=true';
  downloadLink.download = fileInfo.filename;
  downloadLink.click();
}
```

## 代码示例集合

### Python - 后端处理（推荐方式）

```python
from src.config.paths import Paths

def process_uploaded_file(tenant_id: str, user_id: str, thread_id: str, filename: str):
    paths = Paths.get()
    thread_dir = paths.tenant_user_thread_dir(tenant_id, user_id, thread_id)
    file_path = thread_dir / "user-data" / "uploads" / filename

    with open(file_path, 'rb') as f:
        content = f.read()

    return content
```

### Python - 虚拟路径解析

```python
from src.config.paths import Paths

def resolve_virtual(virtual_path: str, tenant_id: str, user_id: str, thread_id: str) -> str:
    paths = Paths.get()
    return paths.resolve_virtual_path_ctx(virtual_path, tenant_id, user_id, thread_id)
```

### JavaScript - 前端访问

```javascript
// 列出已上传的文件
async function listUploadedFiles(threadId) {
  const response = await fetch(`/api/threads/${threadId}/uploads/list`);
  const data = await response.json();

  data.files.forEach(file => {
    console.log(`文件: ${file.filename}`);
    console.log(`下载: ${file.artifact_url}?download=true`);
    console.log(`预览: ${file.artifact_url}`);
  });

  return data.files;
}

// 删除文件
async function deleteFile(threadId, filename) {
  const response = await fetch(
    `/api/threads/${threadId}/uploads/${filename}`,
    { method: 'DELETE' }
  );
  return response.json();
}
```

## 注意事项

1. **多租户隔离**
   - 所有物理路径都包含 `tenant_id` 和 `user_id`，确保数据隔离
   - `ThreadContext`（冻结 dataclass）在系统入口处验证身份信息并贯穿全链路
   - `Paths` 单例提供路径遍历防护，防止跨租户访问

2. **Agent 使用**
   - Agent 只能看到和使用 `virtual_path`（`/mnt/...`）
   - 沙箱系统根据当前 `ThreadContext` 自动映射到对应租户的实际路径
   - Agent 不需要知道实际的文件系统结构和多租户层级

3. **前端集成**
   - 始终使用 `artifact_url` 访问文件
   - 不要尝试直接访问文件系统路径
   - 使用 `?download=true` 参数强制下载

4. **Markdown 转换**
   - 转换成功时，会返回额外的 `markdown_*` 字段
   - 建议优先使用 Markdown 版本（更易处理）
   - 原始文件始终保留

5. **沙箱状态独立存储**
   - 沙箱状态存储在 `sandbox_state/{thread_id}/` 下，独立于用户数据目录
   - 这允许沙箱生命周期独立于用户数据管理
