# DeepOneR 汉化 Mod APK 构建器

用一份 DeepOneR 原版 Android APK 构建可安装的简体中文 Mod。游戏资源由官方更新，启动器按本地游戏资源版本加载对应的汉化脚本。日常汉化脚本更新通常只需重新启动游戏。

## 打包依赖哪些上游文件

构建器使用 `Lisanjin/DeepOne_translate_CN` 提供的 Android 原生汉化脚本和中文字体。首次运行 `prepare_assets.py` 时，脚本按 `vendor/pinned.json` 中记录的提交下载以下四个文件，校验 SHA-256 后保存在本地 `vendor/` 目录。无需克隆整个上游仓库。[上游汉化仓库](https://github.com/Lisanjin/DeepOne_translate_CN)

| 上游文件 | 打包时的用途 |
| --- | --- |
| `assets/Humming-D.ttf` | 替换游戏字体，并随 APK 提供启动器使用的中文字体副本。 |
| `assets/src/project.js` | 作为 APK 内置的汉化补丁。启动器在游戏资源版本匹配时加载它；APK 中的原版脚本也会保留。 |
| `assets/project.manifest` | 提供汉化资源版本、脚本大小和 MD5，用来核对下载的脚本并生成内置补丁的版本信息。 |
| `assets/version.manifest` | 更新本地补丁快照时，用来核对它与 `project.manifest` 声明的资源版本是否一致。 |

另外还需要**用户自行提供的原版 APK**，构建器从中读取游戏资源、启动代码和官方更新地址。签名用的公开测试密钥和证书由 `prepare_assets.py` 从 AOSP 下载并校验。[AOSP 签名材料](https://github.com/aosp-mirror/platform_build/tree/android-14.0.0_r1/target/product/security)

工具和素材准备齐全后，普通构建只读本地文件，可以离线打包。只有主动刷新补丁快照时，构建器才会再次访问汉化仓库。

## 从原版 APK 构建

以下命令在仓库根目录的 Windows PowerShell 中运行。原版 APK 可放在任意目录，路径含空格时保留引号。

1. 安装 Python 3.11+、Java 17+、Node.js 和 Android SDK Build Tools。Java 和 Node.js 需能从命令行启动。Build Tools 可通过 Android Studio 的 SDK Manager 安装，建议使用 36.0.0，其中包含 `aapt`、支持 `-P 16` 的 `zipalign` 和 `lib/apksigner.jar`。
2. 建立虚拟环境并安装 Python 依赖。

   ```powershell
   python -m venv .venv
   .\.venv\Scripts\python.exe -m pip install -r requirements.txt
   ```

3. 下载并校验默认构建素材。

   ```powershell
   .\.venv\Scripts\python.exe prepare_assets.py
   ```

4. 将示例路径换成你的原版 APK，开始构建。

   ```powershell
   .\.venv\Scripts\python.exe build.py "D:\Games\DeepOneR 原版.apk"
   ```

   也可以把 APK 拖到 `build.cmd` 上，或运行 `.\build.ps1 -Apk "D:\Games\DeepOneR 原版.apk"`。两个入口都会优先使用上一步建立的虚拟环境。

5. 从 `output/` 取出签名后的 APK。同名的 `.sha256` 文件给出该 APK 的校验和，`.build.json` 记录原版 APK 摘要、输出包名、签名和内置汉化的上游提交。构建器会检查 APK 签名、ZIP 对齐和注入文件，并核对主 DEX、原生库和原版游戏脚本的内容。

## 如何更新汉化

**想让新打的 APK 内置较新的汉化脚本**，可在准备好素材后使用 `--refresh`。它会先下载配置分支（默认 `main`）当前的汉化脚本和两份清单，再打包。字体继续使用已校验的本地文件。

```powershell
.\.venv\Scripts\python.exe build.py "原版.apk" --refresh
```

如果需要复现上游的某次修改，可用 `--ref` 指定提交编号、标签或分支。下面的例子会把 `main` 在**命令运行时**指向的提交保存为本地快照，供下次打包使用。把 `main` 换成完整提交编号，即可准确选择同一个版本。再次运行 `prepare_assets.py` 会切回仓库默认的快照。

```powershell
.\.venv\Scripts\python.exe refresh_upstream.py --ref main
```

**已经安装的 APK** 会在游戏启动时独立检查汉化更新。启动器从上游的 GitHub Pages 获取清单，必要时尝试同仓库的 raw 地址。只有资源版本与游戏一致、脚本大小和 MD5 校验通过时，才加载对应补丁。上游暂时不可用时，仍可使用匹配的本地补丁；没有匹配补丁时，游戏使用官方脚本。打包时选定的提交只决定 APK 内置哪份汉化，不限制之后的游戏内热更新。

## 安装与升级

- 原版与 Mod 使用不同的包名，可以共存。Mod 包名为 `jp.co.dmm.dmmgames.deeponer_a_mod`，桌面名称为 **DeepOneR Mod**。
- 升级已安装的 Mod 时，请使用相同签名和更高的 Android `versionCode`，直接覆盖安装以保留数据。默认版本号取 `config.json` 的 `version_code_floor`、原版 versionCode + 1、上次成功构建的 versionCode + 1 三者中的最大值。本地历史保存在 `.build-state.json`。换电脑构建时可用 `--version-code` 显式指定版本号。
- 为兼容旧 Mod，构建器使用 AOSP 公开测试签名。任何人都能用这把密钥签名，请从可信渠道取得 APK，并用对应的 SHA-256 核对文件。

## 常见问题

| 情况 | 处理方法 |
| --- | --- |
| 找不到 Android Build Tools | 设置 `ANDROID_SDK_ROOT`，或在构建命令后加 `--build-tools "C:\Android\Sdk\build-tools\36.0.0"`。Java、Node.js 可用 `--java`、`--node` 指定完整路径。 |
| 提示素材缺失、哈希不符 | 重新运行 `prepare_assets.py`，确认能访问 GitHub。修复后若需要较新的快照，再运行 `refresh_upstream.py`。 |
| 包名、启动结构或字体无法识别 | 使用包名为 `jp.co.dmm.dmmgames.deeponer_a` 的原版 APK。若新原版改变了启动流程、引擎或资源布局，需适配构建器。 |
| 安装提示版本过低或签名冲突 | 核对已安装 Mod 的 versionCode 和签名。版本过低可提高 `--version-code` 后重建；签名不同的包无法覆盖升级。 |
| 需要查看打包过程 | 查看 `build/` 下本次构建的日志。加 `--keep-intermediates` 可保留中间 APK。完整参数见 `build.py --help`。 |

## 目录与许可

| 路径 | 用途 |
| --- | --- |
| `build.py`、`lib/` | 构建入口及 Android 二进制 Manifest、资源表编辑器 |
| `runtime/` | 热更新、旧缓存迁移和家具数据兼容处理 |
| `prepare_assets.py`、`vendor/pinned.json` | 准备默认素材并验证摘要 |
| `refresh_upstream.py` | 选取另一份汉化快照，校验脚本与清单 |
| `vendor/` 下的本地下载、`build/`、`output/` | 素材缓存、构建日志和产物，由 Git 忽略 |

游戏资源、汉化素材和 AOSP 签名材料沿用各自权利人的条款。构建器自有代码的开源许可证尚待选择。
