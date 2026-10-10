"""Build the offline novice guide and plain-text troubleshooting handbook."""
from pathlib import Path
import html
import json
import re

ROOT = Path(__file__).resolve().parents[1]
ITEMS = []
def add(category, question, answer):
    ITEMS.append({"category": category, "question": question, "answer": answer})

DATA = r'''
安装与第一次使用|这是完整包还是更新包？朋友没有旧版能用吗？|这是完整安装入口，已包含 2.0 程序，保留此前 1.5 的字幕功能。不需要先装 1.0，再一层层打补丁。
安装与第一次使用|到底要点哪个文件？|先把 ZIP 解压，再双击“字幕工坊_安装与启动.exe”。出现安装窗口后，保持第一项推荐设置，点击“一键安装 / 修复”。source、payload、tests 是附带源码与测试，普通使用不用打开。
安装与第一次使用|怎样解压 ZIP？|在下载到的 ZIP 上点鼠标右键，选择“全部解压缩”，再点“提取”。打开新出现的文件夹。如果仍看到压缩工具的工具栏或 ZIP 文件名，先返回解压后的普通文件夹。
安装与第一次使用|需要自己装 Python、pip 或输入命令吗？|正常情况下不需要。安装器下载官方的完整 Python 运行包，配置到字幕工坊自己的目录，再安装固定版本组件。不要自行复制网上的命令或改系统 PATH。
安装与第一次使用|需要安装 CUDA 吗？|默认 CPU 模式不需要。确有 NVIDIA 显卡时，可以勾选安装器的可选加速项，或在软件设置里点“启用显卡加速”；程序准备自己的运行组件，不要求手动安装整套 CUDA 开发工具。
安装与第一次使用|安装时会改动电脑上已有的 Python 吗？|本懒人包使用独立运行目录，不依赖或更换现有 Python，也不添加系统 PATH。若识别组件缺少 Windows VC++ 运行库，可能调用微软安装器补齐该系统运行库。
安装与第一次使用|支持什么电脑？Mac、手机能用吗？|本包面向 Windows 10/11、64 位 Intel 或 AMD 电脑。不是 macOS、安卓、iPhone 安装包；32 位 Windows 和 Windows ARM 电脑不属于本版支持范围。电脑“设置 → 系统 → 系统信息/关于”可查看系统类型。
安装与第一次使用|应该选择哪种安装内容？|第一次选 small 完整版。tiny 最小、下载与占用最低，适合老电脑，准确度略低；Turbo 文件和资源需求更大，适合明确需要它、且电脑资源充足时选择。已有 SRT、只想翻译或校对，可选仅 SRT 模式；以后再补装识别组件。
安装与第一次使用|文件这么小，是不是没装完整？|小包是完整的自动安装入口，包含软件程序与说明。运行环境和模型在第一次安装时从原发布站点下载，所以本包不是所有大组件都塞在里面的离线整合包。
安装与第一次使用|第一次到底要下载多少？|small 推荐模式约 0.61 GB；Turbo 模式约 1.75 GB；tiny 模式约 0.2 GB；仅 SRT 模式约 35 MB。可选 NVIDIA 加速另约 570 MB，缺少 VC++ 运行库时还可能补下约 26 MB。均为本版清单估算，重试流量另计。
安装与第一次使用|电脑要留多少空间？|安装器对 small 要求约 3 GB 空闲，对 Turbo 要求约 7 GB，对 tiny 要求约 1.5 GB，对仅 SRT 要求约 0.5 GB；解压、缓存和运行文件都会占空间。视频及导出文件另外占空间，输出目录可选 D 盘。
安装与第一次使用|能改成安装在 D 盘吗？|本版安装器固定放在当前用户的数据目录，尚未提供安装盘选择。可把视频和输出目录放 D 盘；C 盘实在不足时先选仅 SRT 模式。不要直接搬动已经安装的 Python 目录。
安装与第一次使用|需要管理员权限吗？|主体安装放在当前用户目录，通常不用。只有缺少微软 VC++ 运行库时可能出现 Windows 权限确认；应核对该运行库安装器的发布者是 Microsoft。不要在不知道用途时随意给其他安装器提权。
安装与第一次使用|出现“未知发布者”或 SmartScreen 怎么办？|本自制启动器没有商业代码签名，可能出现信誉提示。先核对文件来自这次交付、文件名和包内校验清单，再决定是否运行。若明确报告病毒/木马，先停止并保留截图；不要关闭杀毒、关闭防火墙或添加整盘排除项来强行安装。
安装与第一次使用|提示“此应用无法在你的电脑上运行”？|先确认是 Windows 10/11 的 64 位 Intel/AMD 系统；重新完整下载并解压，排除文件不完整。若系统由学校/公司管理，交给管理员确认是否允许运行，不要绕过组织的应用限制。
安装与第一次使用|装好以后每次还点安装器吗？|正常只需双击桌面的“字幕工坊”。没有快捷方式时，打开原安装器，点“启动字幕工坊”。安装包可以留着，用于修复和看说明。
下载、联网与续传|安装要联网，使用时也一定要联网吗？|首次下载组件与模型需要联网。准备好的本地模型可离线识别，SRT 校对、加字幕、打轴和导出也可离线；DeepSeek 翻译需要联网。切换到未准备的识别模型还会产生新下载。
下载、联网与续传|卡在 0%、长时间没有变化？|先看当前文件名和 MB 数字是否增长。首次连接、解压、校验也需要时间；不要连续重复开多个安装器。若日志持续报连接超时，取消后换一个正常可用的网络，再点同一个安装按钮。
下载、联网与续传|进度已经 100%，为什么还在装？|进度条显示当前文件，不是所有步骤的总进度。一个文件下载到 100% 后，还会校验、展开、安装其他组件或模型；最终看到“安装完成”才算结束。
下载、联网与续传|提示连接超时、无法解析主机或连接被重置？|确认电脑能正常上网、没有停留在校园网/酒店的登录页面。尝试完成网络登录或换手机热点后重试。不同阶段访问 python.org、files.pythonhosted.org、huggingface.co；可选 GPU 访问 NVIDIA 下载站。
下载、联网与续传|下载一半断网、关机了，要从头来吗？|安装器会保留已校验的完整文件和 .part 片段，重新打开后尽量续传；服务器不支持续传时会重下当前文件。不要删除缓存，也不要把 .part 改名冒充完整文件。
下载、联网与续传|提示“校验不通过”是什么意思？|下载到的文件与清单中的 SHA-256 不一致，程序没有运行它。可能是中断、损坏或网络返回了登录页；错误片段会被丢弃。换网络后重试，不要自行修改清单里的校验值。
下载、联网与续传|提示“续传范围错误”？|服务器或中间代理返回的数据与续传位置不匹配，程序会停止拼接以免生成坏文件。先换网络并重试；仍失败时导出安装诊断，保留缓存，避免盲目清空全部大文件。
下载、联网与续传|证书验证失败、SSL、TLS 报错？|检查电脑日期、时间和时区是否明显不对，先完成正常系统更新。学校或公司网络有证书代理时请咨询管理员，或用其他正常网络测试。不要关闭证书校验，也不要安装来历不明的根证书。
下载、联网与续传|Hugging Face 模型下不下来？|在其他正常可用网络下重试；模型较大，先用 small。若朋友已经有完整 faster-whisper 模型，可先选仅 SRT 模式安装程序，再补装识别组件并在设置中指定模型目录；本安装器的完整模式仍会准备所选模型，不会自动识别任意外部目录。
下载、联网与续传|tiny 模型从哪里下载？|tiny 模型文件托管在魔搭 ModelScope（国内可直接访问），small 与 Turbo 来自 Hugging Face 官方仓库并支持国内镜像。所有模型文件都按清单逐文件校验 SHA-256，来源不同不影响文件安全。
下载、联网与续传|“国内镜像”是什么，要不要勾？|默认勾选即可。huggingface.co 在国内可能连不上或很慢；勾选后模型文件下载失败会自动改用 hf-mirror.com 镜像重试，SHA-256 逐文件校验不变，文件安全不受影响。该选项只对 Hugging Face 模型地址生效；Python 运行包和识别组件仍从官方站点下载。
下载、联网与续传|主程序自己下识别模型，是从哪里下的？|1.6.1 起主程序也支持自己下模型：先试魔搭 ModelScope（国内直连，实测比镜像快很多），魔搭没有的再走 hf-mirror.com 镜像，最后才试 huggingface.co。下载会显示已下多少 MB，可以中断，下次接着下，已经下好的部分不会重下。
下载、联网与续传|主程序下模型失败了怎么办？|程序会先花几秒探测线路，自动绕开已经失效的代理（比如代理软件没开、端口变了）。仍然失败时：先关掉代理软件重试，或把“识别模型”换成本机已有的 small / turbo。模型文件在安装目录的 models\downloaded\<模型名>\，删掉该文件夹即可重新下载。
下载、联网与续传|能用朋友已经下载的模型吗？|在“翻译与识别设置 → 本地模型文件夹”选择完整目录，里面至少应有 model.bin、config.json、tokenizer.json，以及该模型配套词表。不要只拷一个 model.bin，也不要选 .pt 或原始 PyTorch 模型。电脑仍须先安装语音识别组件。
下载、联网与续传|切换网络或开关代理后还是报错？|停止任务，关闭并重新打开程序后再试，让进程重新读取网络配置。若系统代理指向已经关闭的软件，先恢复正确的系统代理设置；不要随意改 API 域名或使用别人给的中转密钥。
下载、联网与续传|安装完了，可以把安装缓存删掉吗？|关闭安装器和字幕工坊后，安装目录下 Easy\cache 是下载/安装缓存（默认安装目录是 %LOCALAPPDATA%\SubtitleStudio，装在别的盘时就是那个盘上的安装目录，右键桌面快捷方式看“起始位置”即可确认），可以删除以腾空间，但将来修复可能重新下载。不要删除 models、settings.json 或你的项目输出目录；已有 GPU 缓存是另一处目录，别混着清理。
打开、修复与界面|双击之后没有窗口或一闪而过？|先关掉重复打开的窗口，再打开懒人安装器点“启动字幕工坊”。仍失败就点“导出安装诊断”；不要直接双击 app.py 或 python.exe。安装器本身也打不开时，发送提示截图、系统类型和文件名。
打开、修复与界面|提示“字幕工坊已经打开”？|用 Alt+Tab 或任务栏切换到原窗口。它可能被其他窗口遮住。不要为了找窗口连开多份识别任务；确认程序无响应后再通过任务管理器结束，并按恢复说明重新打开项目。
打开、修复与界面|“找不到方法 String.Split”又出现了？|本懒人安装器使用微软 .NET Framework 4.5 参考库编译，已避开此前那类接口错误。先确认打开的是本包 2.0 启动器，而不是旧 B 站下载器或旧文件；仍出现就发完整错误截图及安装诊断，不要重装 Python 来碰运气。
打开、修复与界面|ModuleNotFoundError、缺少 faster_whisper / av / onnxruntime？|如果先装了仅 SRT 模式，这是尚未安装识别组件；重新运行安装器，选择 small 或 Turbo 后安装。若之前已装完整版，关闭主程序后点“一键安装 / 修复”，不要手动逐个找包装。
打开、修复与界面|DLL load failed、VCRUNTIME140、MSVCP140？|关闭字幕工坊后运行安装 / 修复。安装器会在需要时尝试补齐微软 VC++ x64 运行库；若提示重启，重启电脑后再试。不要从 DLL 下载网站单独拷文件。
打开、修复与界面|E102、E201、E202、E401 怎么看？|E102 是 Python/窗口运行环境未启动；E201 是识别组件安装未完成；E202 是安装后仍无法载入原生组件；E401 是首次设置写入失败。先关闭旧窗口并重试一次；仍失败导出安装诊断，它会保留阶段与具体错误类型。
打开、修复与界面|Permission denied、拒绝访问、文件正在使用？|关闭正在运行的字幕工坊和重复的安装器，再试。检查安全软件是否给出了明确拦截提示。不要把程序放在 Windows、Program Files 等受保护位置，也不要用“管理员运行所有文件”当通用解决方法。
打开、修复与界面|学校电脑提示被管理员阻止？|让管理员确认是否允许使用本工具，必要时提供源码和依赖清单。本包不绕过学校、单位的应用白名单、网络限制或权限政策。
打开、修复与界面|窗口太大，按钮看不全？|先尝试最大化。设置页本身可滚动；低分辨率屏幕可临时把 Windows“显示缩放”调小一档。新版本已降低小屏幕窗口的最小高度；仍有遮挡时发完整窗口截图和屏幕分辨率。
打开、修复与界面|新包运行后没有桌面快捷方式？|在安装器点击“启动字幕工坊”即可。桌面重定向或权限限制可能阻止创建快捷方式；不影响程序主体。快捷方式损坏时再次完成安装可重新生成。
打开、修复与界面|可以复制整个安装目录到另一台电脑吗？|推荐给朋友转发这份干净的小安装包，让它在朋友电脑上自动安装。不要打包自己的整个用户数据目录，里面可能有设置、术语和缓存；旧快捷方式也可能指向原电脑。已有模型可单独转交完整模型目录。
打开、修复与界面|怎么卸载？会删掉做好的字幕吗？|先关闭程序，删除桌面的“字幕工坊”快捷方式，再按需删除整个安装目录（默认是 %LOCALAPPDATA%\SubtitleStudio，装在别的盘时就是那个盘上的目录）。Easy 是程序，models 是模型，gpu-runtime 是显卡组件，需要彻底清理时一并删除。你的输出目录中的 project.json、SRT、TXT 不在这里，先备份后自行决定是否删除。
打开、修复与界面|为什么 360、杀毒软件会拦截这个软件？是不是有病毒？|不是病毒，这是“没签名 + 没名气”的常见待遇。本程序没有购买商业代码签名（个人项目一年要三千多元），下载量也少，安全软件对这类程序一律从严；360 等软件的“文档保护 / 反勒索”还会保护 文档、桌面、视频 等文件夹，只拦截本程序写入，受信任的程序不受影响。想确认有没有真问题：包内每个文件都有 SHA-256 校验清单，源码和测试随包附带，可自己核对；程序不会把你的视频上传到任何地方，只有翻译时会把待译文本发给 DeepSeek。处理办法：把软件目录加入信任区，或关闭“文档保护 / 反勒索服务”，或把输出目录改到 D 盘等非受保护位置。千万不要关闭杀毒、关闭防火墙或添加整盘排除项来强行安装；如果安全软件报出了具体的病毒名或木马名，请先停止并保留截图。
打开、修复与界面|程序卡住、闪退、没反应怎么办？|先等一会儿，长视频识别期间界面可能短暂无响应；不要在任务运行时连点，也不要重复开程序。仍然卡死时用任务管理器结束进程，重新打开后用“打开项目 / SRT…”载入原 project.json 继续。反复闪退请到“诊断与资源”导出诊断包，连同最后点的按钮一起发来；不要直接双击 app.py，也不要重装系统。
账号、密钥与费用|API 密钥是什么？安装时必须填吗？|它是让软件调用你自己的 DeepSeek API 账户的凭据。安装、识别、已有 SRT 校对和打轴不需要；只有准备让软件自动翻译缺少的中文时才填写。
账号、密钥与费用|去哪里取得密钥？|打开 https://platform.deepseek.com/，登录自己的账户，在平台的 API Keys/密钥管理处创建密钥并复制。网站界面可能变化，以官方平台为准；不要用来路不明的“共享 key”。
账号、密钥与费用|把密钥填到哪里？|打开字幕工坊，点击“翻译与识别设置”，把密钥粘贴到“API 密钥”。默认翻译模型保留 deepseek-flash，回到“任务与字幕”点“识别并翻译 / 继续”。输入框用圆点隐藏内容是正常现象。
账号、密钥与费用|为什么重开软件后密钥没了？|默认只在当前运行期间使用，不写入设置、项目或导出文件，所以重开后需要重新粘贴。需要省事时可在设置页勾选“记住密钥”：密钥会用 Windows 账户级加密保存在本机，仅当前 Windows 用户在本机才能解开。不要为了省事把真实密钥写进转发给朋友的安装包或说明书。
账号、密钥与费用|“记住密钥”安全吗？|勾选后密钥经 Windows DPAPI 按当前用户加密后存放在本机用户目录，其他系统用户、其他电脑和诊断包都无法直接读取；程序与项目文件里不保存明文密钥。换电脑、换系统用户或重装系统后需要重新粘贴。不想保留时取消勾选即可清除已存密钥。
账号、密钥与费用|DeepSeek 网页能免费聊天，软件翻译也免费吗？|这是通过 DeepSeek API 调用翻译，计费与网页版聊天不是同一回事。以朋友自己的 API 平台余额和账单为准。本地识别、手动校对和导出本身不产生 DeepSeek API 调用。
账号、密钥与费用|我有 ChatGPT 会员，能用来抵扣吗？|不能把 ChatGPT 会员当作这个软件的 DeepSeek API 密钥或余额。此版本固定调用 DeepSeek 官方接口；要翻译，使用对应平台自己的 API 凭据。
账号、密钥与费用|HTTP 401 怎么处理？|密钥认证失败。重新复制完整密钥，去掉首尾多余空格，确认没有把密码、模型名或其他平台的 key 填进去。必要时在官方平台撤销旧密钥并创建新密钥。
账号、密钥与费用|HTTP 402 怎么处理？|API 账户余额不足。到 DeepSeek 官方平台查看余额和计费情况，按自己的预算决定是否充值；之后打开原项目继续。不要反复重识别视频。本软件本身免费，不存在退款一说；API 充值与退款按 DeepSeek 官方平台的政策处理。
账号、密钥与费用|HTTP 400、403、404 怎么处理？|400 先检查模型名称和参数是否改过；404 检查模型是否仍可用；403 查看账户权限或站点访问是否被拒绝。先恢复本包默认的 deepseek-flash 再试；仍失败保留错误码，不要随便换 API 域名。
账号、密钥与费用|HTTP 429、500、502、503、504 怎么处理？|通常是限流或服务暂时异常。程序会做有限次重试；失败后先等一会儿，再打开同一个 project.json 继续，不要不停连点。已保存的译文会被复用。
账号、密钥与费用|超时失败还会收费吗？|有可能。服务端可能已经处理请求，但响应没有到达你的电脑，重试也可能产生调用费用。软件的 token 计数只覆盖收到响应的情况，最终以官方账单为准。
账号、密钥与费用|怎样避免重复烧 token？|优先“打开项目 / SRT…”载入以前的 project.json 或对应原文/中文 SRT。普通继续只补译中文为空的条目；不要勾“重新翻译全部”。只调时间、手改文字、导出文件都不会调用翻译 API。
账号、密钥与费用|改了原文，为什么中文没自动重译？|这是为了保留你已经做过的译文。若确实要重译该条，清空这条的中文并保存，再点“识别并翻译 / 继续”；其他已有译文保持不变。
账号、密钥与费用|人名、术语翻得不统一怎么办？|先在“人名 / 术语说明”写统一对应关系，再翻译缺失条目。已经有中文的条目不会因为改术语而自动重新收费翻译；可手改，或只清空确实要重译的几条。
账号、密钥与费用|翻译到底花多少钱？翻译前会告诉我吗？|会。发起翻译前软件先弹费用预估（条目数、预计 token 与估算金额），必须点确认才会真正调用翻译；不确认不扣费。全新视频会先本地识别、拿到原文后再按真实内容估价，更接近实际。最终以 DeepSeek 官方账单为准，预估值只作参考。
账号、密钥与费用|为什么点“识别并翻译”，它先识别了一会儿才弹费用？|对全新视频，软件先免费完成本地识别，再按识别出的原文条数估算翻译费用并弹窗确认；确认后才开始翻译。识别阶段不产生任何 API 费用。
账号、密钥与费用|队列批量翻译怎么确认费用？|加入队列的文件会在开始前统一弹一次总确认，每个文件的预估金额写入日志；开始后依次处理，不再逐个弹窗。金额仍是估算，实际以官方账单为准。
账号、密钥与费用|DeepSeek 到底怎么收费？为什么有时候贵一倍？|它按峰谷计费：北京时间周一至周五（不含中国法定节假日）9:00-12:00、14:00-18:00 为高峰时段；其余时段，包括周末及中国法定节假日全天，均为空闲时段，空闲时段单价为高峰时段价格的一半。调休上班的周末同样按空闲计费。软件在翻译前的费用弹窗里会同时列出两档金额，并写明现在按哪一档计费，你可以自己决定现在做还是等到空闲时段再做。输入按“缓存未命中”的单价估算，命中缓存会更便宜，所以估算只会偏高不会偏低。官方调价后软件可能还没跟上，请以官方价格页为准。
识别视频与模型|可以一次处理多个视频吗？|可以。点击任务页的“添加多个文件…”把视频或 SRT 加入队列，再点“识别并翻译 / 继续”或“仅识别原文”。队列会依次处理每个文件，单个失败会自动跳过并继续，完成后统一提示。翻译模式下费用预估写入日志而不弹窗；点“停止”会中止整个队列。
识别视频与模型|第一次该拿什么视频试？|选一个自己能正常播放、有人清楚说话、约 30 秒至 1 分钟的本地视频，先点“仅识别原文”。这样能快速确认声音、模型和输出位置，再处理长片。
识别视频与模型|视频链接能直接粘进去吗？|不能，本工具处理电脑上的视频、音频或 SRT。先用合适方式把视频保存到本机，再点“选择文件”；这份安装包不包含 B 站或油管下载器。
识别视频与模型|哪些语言比较适合？|界面重点提供英语、日语和自动识别，目标译文是简体中文。知道源语言时优先手动选对。模型还可能识别其他语言，但本包没有逐一验证所有语言的效果。
识别视频与模型|能把中文翻成日文或英文吗？|此版翻译目标固定为简体中文；不提供任意目标语言切换。日语视频的“原文”是日文，中文是其译文；不要把“原语言”下拉框当作目标语言选项。
识别视频与模型|small 和 Turbo 怎么选？|朋友第一次用先选 small，文件与资源负担较小。Turbo 是更大的多语言识别模型，并不保证在每台 CPU 上更快或所有片段都更准；确认短片效果和电脑负担后再选择。
识别视频与模型|换了模型，但好像还是旧结果？|打开已有项目时，程序优先复用项目里的原文，不会因为改了模型而自动重识别。需要用新模型重新识别时，重新选择原视频创建另一份任务，保留原项目作备份。
识别视频与模型|选了 Turbo，为什么仍在用本地 small？|“本地模型文件夹”非空时优先使用该目录。清空这个框，再选择 Turbo；或者将它改为完整 Turbo 模型目录。懒人安装器准备的 small/Turbo 可由下拉框直接选择，不必手填路径。
识别视频与模型|提示“没听出任何说话声”、识别出来是空的？|报错窗口里会列出「我实际看到的是」，先看那几行：「识别出几段」是 0 说明声音根本没进去——先播放原视频，确认它不是没有声音（没声音、误静音、选错音轨都会这样）；「识别出很多段但全没文字」多半是语言选错了，在「识别语言」里手动选一次正确语言再试。如果原视频的声音小、听不到人声，先在剪辑软件里单独导出那条音轨并适当增益，再交给本软件。只有背景音乐、没有人讲话的片段识别不出来是正常的。（旧版本这里提示的是“未识别到说话声”，意思一样。）
识别视频与模型|视频画面有字幕，为什么识别不出来？|本程序做语音识别，不做画面文字 OCR。没有人声、只有画面文字的片段，需要先取得文字或原文 SRT，再导入翻译/校对。
识别视频与模型|识别内容重复、胡话、与视频不符？|先确认源语言和音轨；音乐、噪声、沉默、多人重叠讲话都会影响结果。先切短片检查，必要时选择更合适模型并人工校对，不要直接把未核对的识别稿当成最终字幕。
识别视频与模型|无法读取视频、没有音轨或解码失败？|用播放器确认文件完整且确实有音轨。尝试在剪辑软件中导出该视频的 WAV 或常见音频文件后再处理；受保护、损坏或特殊音轨的文件不保证能解码。
识别视频与模型|识别时占内存、CPU 很高、电脑卡，是不是坏了？|音频解码、人声检测、特征计算，以及 CPU 模式推理都会占用资源，短时间占用高不等于坏了。先关闭其他大型程序，用短片和 small 测试；长片可按段处理。若长时间电脑卡到无法操作、内存逼近上限或报错，停止任务并导出诊断，不要只凭瞬时占用判断。
识别视频与模型|能保证一小时视频几分钟完成吗？|不能。速度取决于 CPU/GPU、模型、音频长度、噪声、磁盘和翻译网络。用 1 分钟同类素材实测更有参考价值，且不同阶段并不一定线性增长。
识别视频与模型|识别中途停止，会保留前半段原文吗？|当前版本在完整识别完成后保存可复用的原文项目；识别中途停止通常需要重新识别。翻译阶段则每完成一批就保存，可继续补译。不要把组件下载续传和字幕识别断点恢复混为一谈。
识别视频与模型|几个人同时说话（两个人、多人连麦），识别出来分不清谁在说？|本版会把重叠的语音当成一段来识别，几个声音同时出现时容易串词、漏字或糊成一句。可行做法：按说话人把素材分段后再逐段识别；或者只导出原文 SRT，由人工校对时把角色补上。多人抢话的地方不要指望自动结果完全正确。
识别视频与模型|能自动标出“每句话是谁说的”（说话人区分、角色名）吗？|本版不做说话人区分，不会自动给字幕加角色名。需要角色标注时，在导出的 SRT 里人工补上；也可以把角色称呼写进“人名 / 术语说明”，让翻译保持称呼一致。
识别视频与模型|背景音乐、游戏音效、杂音太多，识别会不会很差？|会。音乐、枪声、引擎声和观众音都会干扰人声检测，容易出现串词或幻觉句。可以先把人声轨单独导出（部分剪辑软件带人声分离），或提高音量后另外导出 WAV 再识别；识别完务必人工校对，不要直接把结果当最终字幕。
识别视频与模型|选了 medium、large-v3 或 base，一直卡在“正在加载模型”？|这三个模型安装时不会预先准备，需要联网下载（large-v3 约 3 GB）。1.6.1 起程序会先弹窗告诉你要下多少 MB，同意后自动从国内源下载，只下这一次。1.6.0 及更早版本不会自动下载，会一直报“连接超时”，请升级到最新版（2.0），或换用 small / turbo。
识别视频与模型|怎么知道哪个模型本机已经有了？|设置里“识别模型”下拉框右边会直接写“本机已有：…”。选中本机没有的模型时，开始任务前会先弹窗说明大概要下多少 MB，点“否”就不下载，换一个本机已有的模型即可。
识别视频与模型|「先分离人声再识别」是什么？我该不该开？|它先把音轨里的说话声与游戏音效、音乐拆开，只拿说话声去识别。默认关闭。判断标准很简单：音效、BGM 明显盖过说话声时打开，能多听出内容；说话本来就清楚的素材打开它，改善有限却要多等约 3 倍时间（实测 60 秒素材从 38 秒变成 101 秒，1 小时素材约多等 37 分钟）。分离全程在本机完成，不联网、不上传。
识别视频与模型|游戏音效、BGM 盖过说话声，识别出来全是乱的，怎么办？|先打开「先分离人声再识别」重跑一次，这是这个功能收益最大的场景。实测把伴奏加强到 3 倍时，不分离的 120 秒素材只识别出 9 个字（几乎全废），分离后恢复到 38 条、306 个字。如果分离后仍不理想，再考虑在剪辑软件里把说话人音量调大、导出单独音轨后识别。
识别视频与模型|开了人声分离，字幕会不会反而变差？为什么默认是关的？|实测两个吵闹档位下都往好的方向，没有观察到变差；素材越吵收益越大。默认关闭只是因为它对安静素材改善有限（约 7 个百分点），却一样要多等 3 倍时间——不是因为它有害。素材本来就清楚，直接不开更省时间。
识别视频与模型|开了「先分离人声」，几个人同时说话就能分清谁在说了吗？|不能。分离只把「人声」和「伴奏」分开，不区分人。几个人同时抢话时，人声之间仍然混在一起，该糊还是糊。它擅长的是「音效盖住人声」，不解决「人声盖住人声」。多人重叠的地方仍建议人工核对，或按说话人分段后分别识别。
识别视频与模型|分离出来的伴奏会不会被上传，或者留在电脑里？|都不会。分离和识别全部在本机完成，不联网、不上传；分离出的临时人声文件在识别结束后立即删除，实测确认不留残留。软件也不会保留伴奏轨，需要伴奏请在剪辑软件里另行处理。
显卡与资源|我的电脑没有 NVIDIA 显卡怎么办？|保持 CPU 模式即可，不要勾显卡加速。Intel 核显、AMD 显卡并不使用本包的 NVIDIA CUDA 加速路径。
显卡与资源|RTX 3050 4 GB 可以用吗？|本软件保留 NVIDIA 的 int8_float16 省显存模式，可以尝试；是否可用仍取决于驱动、模型和其他显存占用。不要把“4 GB 模式”理解为任何素材都保证不超显存。
显卡与资源|为什么 GPU 只占一点显存，CPU 仍很忙？|解码、音频预处理、人声检测和部分数据准备仍会使用 CPU 与系统内存；显存占用量本身不等于计算速度。看“诊断与资源”中的实际设备和阶段，比只盯显存数字更可靠。
显卡与资源|选了 GPU／显卡加速，怎么知道真在用、有没有生效？|在“诊断与资源”里查看实际设备、计算类型和当前阶段。如果觉得显卡不工作、显卡没用、加速没用或干脆没加速，多半是被自动回退 CPU 了：日志会写明回退原因（驱动、显存或组件问题）。仅仅在下拉框选中 NVIDIA 不能证明推理真的用了显卡；笔记本没用独显、只在核显上跑时，先确认设备选的是 NVIDIA。
显卡与资源|没有 NVIDIA 驱动，或驱动太旧？|先在电脑品牌或 NVIDIA 官方渠道确认适合本机的驱动。软件不自动更换显卡驱动；驱动未准备好时用 CPU。不要下载安装来历不明的驱动合集。
显卡与资源|CUDA、cuBLAS、cuDNN 报错？|先关闭其他占显存的软件，运行“启用显卡加速”让程序检查自己的组件。不建议手动把 DLL 复制进 Windows 目录；仍失败先用 CPU，并发诊断包。
显卡与资源|Out of memory、显存不足？|关闭游戏、其他模型程序和占显存的剪辑任务，再试小模型。仍不够就改 CPU；本软件在识别阶段遇到部分 GPU 问题时会回退 CPU，从头完成识别。
显卡与资源|勾了安装显卡加速，但安装结束仍用 CPU？|安装器允许可选 GPU 准备失败后保留可用的 CPU 环境，日志会说明。旧用户的设置也会保留；进入“翻译与识别设置”确认设备，再看诊断页实际设备。
显卡与资源|加显卡能让 DeepSeek 翻译更快吗？|显卡用于本地语音识别。DeepSeek 翻译发生在其远程服务端，主要受网络、服务状态和请求大小影响，换本机显卡不能直接加速这部分。
显卡与资源|开了「先分离人声再识别」，能不能用显卡加速？|不能。分离这一步只走 CPU——随包的 onnxruntime 没有带 CUDA 组件，程序也只请求 CPU 推理。显卡加速只作用于分离之后的语音识别：开着显卡时识别那一段会快，但分离本身的时间不变。素材很吵、又不想等太久时，这是必须接受的代价。
显卡与资源|用显卡识别和用 CPU 识别，出来的字幕一样吗？|模型相同，但两者计算精度不同（显卡走 int8_float16，CPU 走 int8），个别字词可能不同。实测音频里极小的差别就能让 Whisper 换一套断句，所以同一素材换设备后结果不完全一致是正常的，不是哪里坏了。要严格前后对比，请全程用同一个设备。
显卡与资源|识别的时候能不能同时打游戏或渲染视频？|会互相拖慢。用显卡识别时显存和算力被占用，游戏和渲染会变卡，识别本身也可能因显存不足回退 CPU；用 CPU 识别则会占满处理器。跑长素材时最好先让识别跑完再做别的，尤其别在识别中途启动渲染。
SRT、校对与打轴|已有 SRT，不想再识别一遍？|点“打开项目 / SRT…”，按内容选择原文、中文、原文＋中文两份，或混合版。导入与预览在本地完成；仅当你随后要求翻译缺失内容时才调用 API。
SRT、校对与打轴|该选哪一种 SRT 导入模式？|只有外语原稿选“原文”；只有中文选“中文”；有时间一致的原文和中文两份选“两份配对”；每条里原文在上、中文在下选“混合版”。先看预览，确认两侧内容没有反。
SRT、校对与打轴|字幕两条叠在一起、一闪而过、或者来不及看完？|点「时间轴体检…」。它不听音频，只查字幕本身的毛病，分三级列出来：**错误**（两条叠在一起、时间倒着走、整条是空的）、**警告**（太短看不清、太长挂着不走、字太多读不完、间隙太小会闪）、**提示**（成段没有字幕，可能漏了句子）。**双击任意一行能跳到那一条**去看。其中叠字、闪帧、时间倒着走这三类可以点「一键修」——只动时间，文字和译文一个字都不会改，改之前自动备份一份项目文件。太短、太长、读不完这三类软件不自动改，因为那要重排句子，机器做只会把内容搞乱，得你自己看。（注意：本软件**不做**「和音频对不对齐」的检查——在背景音乐铺满的素材上，不引入额外依赖的办法测不准，实测过两种做法都找不回已知偏移，详见源码 timeline_qc.py 开头的记录。整体偏了请用「整体偏移…」。）
SRT、校对与打轴|「时间轴体检」和「待确认…」有什么区别？|「待确认…」只列两样：整理之后剩下的长静音和过短条目，是给你扫一眼的只读清单。「时间轴体检…」是全量检查，覆盖叠字、闪帧、时间倒序、看不清、读不完、成段漏句等，分错误/警告/提示三级，**能双击跳到那一条**，安全的还能一键修。导出之前跑一次体检比较稳妥。
SRT、校对与打轴|「时间轴体检」报了一堆“读不完”，是不是字幕坏了？|多半不是。报“读不完”只说明那条字幕在它那么长的时间里放了太多字，观众跟不上。中文字幕的常规上限是每秒 9 个字，超了就建议拆成两条或缩短。如果报出来的是「按原文（还没翻译）算」，那说明这份还没翻译，软件是拿原文估的，翻译成中文后字数一般会少一些——可以翻译完再复查一次。
SRT、校对与打轴|两份 SRT 为什么配对失败？|配对按每条的开始和结束时间对应。两份文件如果来自不同版本、已经拆合或调过时间，就可能配不上；优先用同一批导出的两份，或先统一时间轴。
SRT、校对与打轴|混合字幕超过两行怎么分语言？|在导入预览里确认“原文占前几行”，按每条实际情况调整并点“确认此条分行”。不要只按字符是不是汉字判断，因为日文里也有汉字。
SRT、校对与打轴|字幕乱码怎么办？|重新导入时尝试合适的文件编码；或者用原字幕软件另存为 UTF-8 的 SRT 再导入。乱码状态下不要直接保存覆盖唯一原件。导出文件使用 UTF-8，日语字体显示问题和编码乱码是两回事。
SRT、校对与打轴|ASS、VTT、TXT 能直接当 SRT 导入吗？|本版正式支持 SRT 和自己的 project.json。其他字幕格式请先在原字幕工具中另存为 SRT；不能只把扩展名改成 .srt。纯 TXT 没有时间轴，无法自动变成已经打好轴的字幕。
SRT、校对与打轴|怎么修改一条字幕？|点选字幕列表中的一条，在下方“原文校对/中文校对”修改，再点“保存此条修改”。检查修改保存后，再继续选其他条目；完成后重新导出用于交付的 SRT。
SRT、校对与打轴|漏识别了一句，怎么补上？|点击“新增字幕…”，填写开始、结束时间和原文，可同时手填中文。保存后会按时间插入；中文留空时，继续翻译只补译缺失条目。
SRT、校对与打轴|怎样给所有字幕统一设 3 秒？|点击“时长设置…”或按 F4，选择“全部字幕”，填 3 秒，确认预览后应用。它保留各条开始时间，只改结束时间，不会把整段字幕重新均匀排列。
SRT、校对与打轴|只想把某一条改成 2.5 秒？|先选中那条字幕，按 F4，选“当前字幕”，填写 2.5 秒并应用。其他条目的时间不会因此整体移动。
SRT、校对与打轴|设置 3 秒，为什么有的只有 1 秒？|默认勾着“遇到下一条开始就结束”，为了避免跨过下一条开始点，会缩短实际持续时间。取消勾选后使用完整时长，但你需要自行检查是否出现重叠。
SRT、校对与打轴|调整时间会消耗 token 吗？|不会。单条/全体时长修改、手改起止时间、加字幕、手动校对和导出都在本地完成。只有发起翻译缺失条目的操作才涉及翻译 API。
SRT、校对与打轴|调错一大片时间，怎么恢复？|有效的时长调整前会在项目旁保存 project_before_timing_日期_编号.json。用“打开项目 / SRT…”打开对应备份，检查正确后再导出；不要删除这些备份直到成片确认。
SRT、校对与打轴|时间格式为什么不接受 1:2:3？|使用 00:00:01,000 这样的“时:分:秒,毫秒”格式；结束时间必须晚于开始时间，分钟和秒小于 60。时长对话框则直接填秒数，如 2.5。
SRT、校对与打轴|只有中文字幕，能还原原文和混合版吗？|不能凭空还原原语言。中文模式可以校对并导出中文 SRT/TXT；要做对应原文和混合版，还需要原文字幕或重新识别原视频。
导出与剪辑软件|“七份文件”是哪七份？|原文 SRT、中文 SRT、混合 SRT，这三种各自的 TXT，以及一份中文 ASS。混合版每条是原文在上、中文在下，共用一组时间轴；ASS 默认白字黑边底部居中，可在设置页导入自己的 .ass 模板并选择样式。手动导出时会先弹勾选面板，可以只勾需要的几项；“空轴”也在同一个面板里。
导出与剪辑软件|为什么只得到两份？|“仅识别原文”或“仅导出原文”只生成原文 SRT/TXT；中文模式也只导出中文两份。需要原文/中文/混合共七份，须有对应原文和完整中文，再点“导出文件…”，在勾选面板里确认对应的几项是勾上的。
导出与剪辑软件|导出的 ASS 是什么，怎么用？|ASS 是带样式的字幕格式，能指定字体、字号、颜色、描边和位置。含中文时导出的“中文_zh.ass”默认白字黑边、底部居中；用 Aegisub 或 VLC、mpv 这类播放器打开即可看到样式。注意 Premiere 不直接支持 ASS：要进 PR 请改用中文 .srt，再用 PR 的“基本图形”调字体和位置；需要特效字幕则先在 Aegisub 里完成。不带样式的场合用 SRT 更通用。
导出与剪辑软件|想用自己的字幕样式，怎么导入？|打开“翻译与识别设置”，在“ASS 导出样式”里点“选择…”，挑一个 .ass 文件当模板；软件会保留它的 Script Info 与样式定义（字体、颜色、位置），并列出模板里的样式名供你选一个。留空就用内置默认样式。模板只是提供样式，正文和时间轴仍由软件生成。
导出与剪辑软件|ASS 字幕字号太大或太小怎么办？|字号写在 ASS 的样式里。用 Aegisub 打开后，点“字幕 → 样式管理器”，编辑当前样式的“字号 Fontsize”即可整体调整；或在软件里换用其它样式。软件本身不另设字号滑杆。
导出与剪辑软件|字幕保存到哪里去了？|在软件里点“打开输出文件夹”。素材项目目录中的 project.json 用来续做；exports 下按时间分开的文件夹是每次导出的成品。不要只在软件安装目录里找。
导出与剪辑软件|为什么校对后，PR 里面还是旧字幕？|校对先修改项目，已经导出的 SRT 不会自动变化。重新导出后，在 PR 中使用最新时间文件夹里的 SRT；不要继续拖旧文件。
导出与剪辑软件|PR 应该导入 SRT 还是 TXT？|要跟视频时间同步，用 SRT；TXT 是阅读和复制用的文稿，没有可供剪辑软件同步的时间轴。把 SRT 导入项目，并与对应原素材的起点对齐。
导出与剪辑软件|PR 里字幕和画面对不上、不同步、整体提前或延后？|字幕以输入文件开头为 0 秒。先检查时间线上视频是不是从别的位置开始，是否删了片头、剪切或变速。本工具不读取 PR 工程，不能自动追踪这些剪辑变化。如果只是固定时长的整体偏移——看起来像“快了一秒”或“慢了一秒”——不用一条条改：点主界面的「整体偏移…」，填 -1（表示提前一秒）或 1（表示延后一秒），选「全部字幕」，点「应用并保存」。整条轨一起挪，每条时长和条目之间的间隔都不变，也不会重新识别或调用翻译。往前挪得太多时，程序不会把时间变成负数，而是整条轨停在 0 秒，并告诉你实际挪了多少。保存前会自动备份一份项目。
导出与剪辑软件|播放器能看视频，为什么 PR 不接受视频？|这是视频编码/容器的兼容问题，与 SRT 字幕不是同一件事。本字幕工具不负责转换视频；可以在剪辑软件支持的流程里准备兼容素材，然后导入对应 SRT。
导出与剪辑软件|可以直接导出带烧录字幕的视频或配音吗？|不能。本版输出字幕和文稿，不改动原视频、不自动配音。需要把字幕固定在画面或调整字体颜色，应在剪辑软件中完成。
导出与剪辑软件|除了 PR，剪映、达芬奇（DaVinci Resolve）、Final Cut Pro（FCPX）怎么用这些字幕？|这三个都能导入 .srt：剪映在“文本 → 本地字幕”里选 srt；达芬奇用“文件 → 导入 → 字幕”，或直接把 srt 拖到时间线；Final Cut Pro 用“导入字幕”把 srt 加到字幕轨道。ASS 只有 Aegisub 和部分播放器能直接用，其他软件一律改用 srt。
导出与剪辑软件|导出的 ASS 样式没生效，或者根本导入不进去？|先确认打开它的软件支持 ASS：Aegisub、VLC、mpv 可以，Premiere 不行（请改用中文 .srt）。用了自己的模板却没变化时，检查设置页“ASS 导出样式”是否选中了模板里的样式名——只选模板文件、没选样式名会退回内置默认样式。模板里指定的字体本机没装，也会被系统替换成别的字体。
导出与剪辑软件|“空轴”是什么？怎么导出只有时间轴、没有文字的字幕？|导出时点“导出文件…”，勾选面板最下面那一项就是“空轴”。勾上后除了原有文件，会多导出一份只有序号和时间轴、正文为空的字幕，格式可选 SRT 或 ASS（ASS 会套用设置里选的样式模板）。空轴默认不勾选，平时导出仍然只有原来的七份。
导出与剪辑软件|想让字幕组或人工翻译帮忙，发什么文件？别人帮我翻完怎么用回来？|发一份空轴就够了：它只有时间轴、没有文字，对方按时间填上正文再发回。如果只要这一份，在勾选面板里只勾“空轴”即可。想让对方看着原文翻，再另外发一份原文 SRT，因为空轴里不含原文。拿回填好的文件后，点“打开项目 / SRT…”，按“中文字幕 SRT”导入，就能继续校对和导出。
导出与剪辑软件|ASS 里的中文显示成方块或问号？|模板里指定的字体本机没装，系统只能用别的字体顶替，顶替不到就显示成方块。用 Aegisub 打开 ASS，在“字幕 → 样式管理器”里把字体改成电脑上已有的（例如微软雅黑），或先安装模板要求的字体再导出。
导出与剪辑软件|我的 .ass 模板里有很多样式，导出时用哪一个？|用设置页“ASS 导出样式”里选中的那一个。只选了模板文件、没选样式名，会退回内置默认样式（白字黑边）。一次只套用一个样式，不会按角色自动切换。
导出与剪辑软件|想让不同角色用不同颜色或位置，能自动做吗？|不能。导出时整份字幕套用同一个样式。需要多角色样式，请在 Aegisub 里建好样式后手工指定，或用剪辑软件的文字工具另做；本软件导出的 ASS 可以直接用 Aegisub 打开继续加工。
保存、恢复与售后|project.json 有什么用？能删吗？|它保存字幕正文、译文、时间和项目状态，是避免重复识别/翻译的关键。成片完成前保留整个项目文件夹；SRT 是交付字幕，但不包含全部项目状态。
保存、恢复与售后|翻译中途断网、没余额或关机，怎么续做？|重新打开软件，用“打开项目 / SRT…”选原来的 project.json，填好自己的密钥，点“识别并翻译 / 继续”。已保存批次的中文会复用；最后一个未保存批次可能重试。
保存、恢复与售后|不小心重选了视频，会重复花钱吗？|同一输入和识别设置通常会找到已保存项目，但移动文件、改模型等可能形成新任务。最稳妥的是主动打开原 project.json，检查列表里已有中文，再继续。
保存、恢复与售后|更新、修复会删掉以前的项目吗？|懒人安装器只处理自己的程序、运行组件与模型准备，保留已有设置；不会清空你选择的输出目录。仍建议在更新前备份重要项目，尤其不要把唯一备份放在安装缓存中。
保存、恢复与售后|发什么给你售后最快？|能打开主程序：到“诊断与资源”点“导出诊断包”。安装或启动失败：在安装器点“导出安装诊断”。再说明版本、最后点击的按钮、报错截图、是否勾 GPU、素材大约多长。
保存、恢复与售后|诊断包里有没有密钥和字幕内容？|程序的诊断包按固定字段记录版本、阶段、设备、资源与错误，不打包视频、字幕正文、术语或 API 密钥；安装诊断也不打包 settings.json。发送截图前仍请检查有没有自己展开的密钥、私人文件名等。
保存、恢复与售后|软件会把视频传到网上吗？|本地识别在电脑上完成。翻译时会把待译原文、必要上下文、部分已译内容和术语说明发送到 DeepSeek 官方接口；并不是完全离线翻译。诊断文件留在本机，不会自动上传。
保存、恢复与售后|API 密钥不小心发给别人了？|到 DeepSeek 官方平台撤销该密钥，创建新密钥，并检查自己的 API 用量。只删除聊天里的图片不能让已经泄露的旧密钥失效。
保存、恢复与售后|朋友也出问题，能直接发你我的诊断包吗？|应让朋友从他自己的电脑导出本次诊断，因为驱动、系统、网络和错误阶段可能不同。不要用你的旧日志代替，也不要转发真实 API 密钥。
保存、恢复与售后|能保证一次安装、所有电脑都绝不报错吗？|不能。这个包把常见准备步骤自动化，并做了依赖校验、续传与诊断；网络、组织限制、驱动和 Windows 环境仍可能不同。先按对应问题处理，仍失败就发诊断，避免让小白盲目重装系统。
'''
for line in DATA.strip().splitlines():
    category, question, answer = line.split('|', 2)
    add(category, question, answer)

def fmt(text):
    return re.sub(r'`([^`]+)`', r'<code>\1</code>', html.escape(text))

QUICK = '''字幕工坊 2.0 懒人完整安装包

转发给朋友：整份 ZIP 即可，不要带上你自己的 API 密钥。

第一次只做这几步：
1. 右键 ZIP → 全部解压缩，打开解压后的文件夹。
2. 双击“字幕工坊_安装与启动.exe”。
3. 保留第一项 small 推荐设置，点“一键安装 / 修复”。
4. 首次需联网下载约 0.61 GB；请预留约 3 GB 空间，等到“安装完成”。
5. 软件自动打开。以后双击桌面上的“字幕工坊”。
下载失败或很慢：安装器里的“国内镜像”默认勾选，请保持勾选。

第一次测试：
1. 选一段 30 秒至 1 分钟、有人清楚说话的本地视频。
2. 选正确原语言，点“仅识别原文”。这一步不需要 API 密钥。
3. 看原文是否正确。需要翻译时，到“翻译与识别设置”填自己的
   DeepSeek API 密钥，再回任务页点“识别并翻译 / 继续”。
4. 完成后点“打开输出文件夹”。在 PR 里使用 SRT，不是 TXT。

如果已经有字幕：点“打开项目 / SRT…”，不要重新识别视频。
安装不顺利：不要重装系统，不要关闭杀毒，不要到处复制 DLL。
先打开“小白指南.html”搜索报错；还不行，在安装器导出安装诊断。
主程序里出问题：到“诊断与资源”导出诊断包。

本包适用 Windows 10/11、64 位 Intel/AMD 电脑。不是 Mac 或手机软件。
它是自动联网安装包，不是把所有大模型都塞进 ZIP 的离线包。
DeepSeek API 密钥需朋友自己取得，翻译按其 API 账户结算。
本包不需要旧版，不含任何个人密钥；完整源码与验证记录一并附带。

关于本软件与第三方组件

作者：lin1024-star（心非）。免费公开给朋友使用，不收费、不出售。
源码与主页：https://github.com/lin1024-star/zimu-studio
本软件自身的代码与文档采用 MIT 协议。

本软件使用了以下第三方组件与模型，版权归各自作者所有，在此致谢：
- Kim_Vocal_2（人声分离）来自 Ultimate Vocal Remover (UVR) 项目，开发者 Anjok07、aufr33 等。
  UVR 的官方说明要求使用其模型的第三方开发者署名，此处署名并致谢。
- faster-whisper、CTranslate2、OpenAI Whisper 语音模型（语音识别）：MIT 许可
- ONNX Runtime（人声分离推理）：MIT 许可
- PyAV / FFmpeg（音视频解码）：BSD / LGPL
- Python 3.13（独立运行环境）：PSF 许可
- NVIDIA CUDA 运行库（可选，仅显卡加速时下载）：NVIDIA 许可

完整清单见本包 THIRD_PARTY.txt；软件里点标题栏右侧的版本号可打开中文说明。
'''

groups = list(dict.fromkeys(x['category'] for x in ITEMS))
faq = []
for i, x in enumerate(ITEMS, 1):
    faq.append(f'<details class="faq" data-category="{html.escape(x["category"])}" id="q{i}"><summary><span>{i:02d}</span>{fmt(x["question"])}</summary><div class="answer"><p>{fmt(x["answer"])}</p><small>{fmt(x["category"])}</small></div></details>')
options = ''.join(f'<option>{html.escape(c)}</option>' for c in groups)
PAGE = '''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>字幕工坊 2.0 · 小白指南</title>
<style>
:root{--ink:#183344;--muted:#5e7482;--accent:#087e86;--bg:#f1f6f8;--border:#d5e3e8}*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.75 "Microsoft YaHei UI","Noto Sans CJK SC",sans-serif}main{max-width:1080px;margin:auto;padding:35px 25px 80px}header{background:#103a46;color:white;padding:35px;border-radius:22px;margin-bottom:24px}h1{font-size:34px;line-height:1.35;margin:9px 0 15px}h2{font-size:23px;margin:32px 0 14px}h3{font-size:18px;margin:0 0 9px}p{margin:8px 0}.eyebrow{letter-spacing:2px;color:#a7dfe1;font-size:13px}.lead{color:#e4eff2;max-width:780px}.tag{display:inline-block;margin:5px 6px 0 0;border:1px solid #698991;border-radius:20px;padding:2px 12px;font-size:13px}.card{background:white;border:1px solid var(--border);border-radius:15px;padding:23px;margin:14px 0}.note{background:#fff8e7;border-left:5px solid #d8a631;padding:14px 18px;border-radius:5px}a{color:#087e86;overflow-wrap:anywhere}header a{color:#adecf0}.steps{display:grid;grid-template-columns:repeat(3,1fr);gap:15px}.step{background:white;border:1px solid var(--border);border-radius:15px;padding:20px}.step b.num{display:inline-grid;place-items:center;background:#d9f1f0;color:#12656a;border-radius:50%;width:35px;height:35px;margin-bottom:12px}.step p{font-size:14px;color:var(--muted)}.mock{border:1px solid #cedde4;border-radius:10px;background:#f6fafb;margin:14px 0;overflow:hidden;font-size:14px}.mock-title{padding:9px 14px;background:#e6f0f4;font-weight:bold}.mock-body{padding:14px}.mock-btn{display:inline-block;color:white;background:#087e86;border-radius:6px;padding:7px 11px;margin:6px 3px 2px 0}.mock-btn.light{color:#174955;background:#dceef0}.mock-row{padding:9px;background:white;border:1px solid #cfdee3;margin:8px 0;border-radius:4px}.arrow{color:var(--accent);font-weight:bold}.caption{font-size:12px;color:var(--muted)}table{width:100%;border-collapse:collapse;font-size:14px}th,td{text-align:left;padding:11px 10px;border-bottom:1px solid var(--border);vertical-align:top}th{background:#eaf3f5}code{font:14px Consolas,monospace;background:#eaf2f5;padding:2px 5px;border-radius:4px;overflow-wrap:anywhere}.search{display:flex;gap:10px;flex-wrap:wrap;padding:16px 0;position:sticky;top:0;background:var(--bg);z-index:2}input,select,button{font:inherit;border:1px solid #b7cdd5;border-radius:8px;padding:10px;background:white;color:var(--ink)}input{flex:1;min-width:220px}button{cursor:pointer;font-size:14px}button:hover{background:#dceef0}.faq{background:white;border:1px solid var(--border);border-radius:10px;margin:10px 0}.faq summary{cursor:pointer;padding:15px 18px;font-weight:600;list-style-position:inside}.faq summary span{font:13px Consolas,monospace;color:#087e86;margin:0 12px 0 5px}.answer{padding:0 22px 17px 48px}.answer small{color:var(--muted)}.muted{color:var(--muted);font-size:14px}footer{border-top:1px solid var(--border);margin-top:35px;padding-top:18px;font-size:13px;color:var(--muted)}li{margin:7px 0}@media(max-width:750px){.steps{grid-template-columns:1fr}header{padding:24px}h1{font-size:28px}main{padding:18px 14px}td,th{padding:8px 5px}.answer{padding-left:22px}}@media print{body{background:white;font-size:11pt}.search,button{display:none}main{max-width:none;padding:0}header{background:white;color:#123a45;border:1px solid #aaa}.lead,.eyebrow{color:#333}.faq{break-inside:avoid}.answer{display:block}details:not([open])>*:not(summary){display:block!important}.steps{grid-template-columns:repeat(3,1fr)}}
</style><main><header><div class="eyebrow">SUBTITLE STUDIO · 2.0 · WINDOWS</div><h1>不用配环境，先做出第一份字幕。</h1><p class="lead">这份指南按第一次接触软件来写。先走完下面三步；出问题时直接搜索你看到的提示，不必从头读完全部说明。</p><span class="tag">完整安装入口</span><span class="tag">CPU 默认可用</span><span class="tag">保留已有字幕与译文</span><p><a href="#first">开始使用</a>　<a href="#money">密钥与费用</a>　<a href="#faq">__COUNT__ 个常见问题</a>　<a href="#support">怎么找售后</a>　<a href="#about">作者与第三方</a></p></header>
<div class="note"><b>先知道三件事：</b>这是 Windows 10/11 x64 软件；第一次安装需要联网下载大组件；自动翻译需要朋友自己的 DeepSeek API 密钥与额度，安装包没有附送账号。</div>
<h2 id="first">第一次使用，照着做</h2><div class="steps"><section class="step"><b class="num">1</b><h3>解压并打开安装器</h3><div class="mock"><div class="mock-title">解压后的文件夹</div><div class="mock-body">📄 字幕工坊_安装与启动.exe<br>📄 先读我_快速开始.txt<br>📄 小白指南.html</div></div><p>右键 ZIP → 全部解压缩。双击第一个 EXE；不要点 source、payload 或 app.py。</p></section><section class="step"><b class="num">2</b><h3>保持推荐，点一次安装</h3><div class="mock"><div class="mock-title">字幕工坊 · 一键准备</div><div class="mock-body"><div class="mock-row">推荐：完整安装 + small</div><span class="mock-btn">一键安装 / 修复</span></div></div><p>首次约 0.61 GB，预留约 3 GB 空间。等“安装完成”，软件会自动打开。普通电脑先不勾 GPU。</p></section><section class="step"><b class="num">3</b><h3>先用短片验证识别</h3><div class="mock"><div class="mock-title">任务与字幕</div><div class="mock-body"><div class="mock-row">视频 / 字幕　选择文件</div><span class="mock-btn">仅识别原文</span><span class="mock-btn light">打开输出文件夹</span></div></div><p>选 30 秒至 1 分钟的人声短片。选对原语言，先只识别原文；确认正常后再翻译。</p></section></div><p class="caption">以上为操作位置示意，用于对应真实按钮名称，并非 Windows 实机截图。</p>
<section class="card" id="money"><h3>需要翻译时，再填密钥</h3><div class="mock"><div class="mock-title">翻译与识别设置</div><div class="mock-body"><div class="mock-row">API 密钥　●●●●●●●●●●</div><div class="mock-row">翻译模型　deepseek-flash</div><div class="mock-row">记住密钥（可选）　☑</div><div class="mock-row">ASS 样式文件（可选）　选择…</div><p class="arrow">填好自己的密钥 → 回“任务与字幕” → 点“识别并翻译 / 继续”</p></div></div><p>密钥在 <a href="https://platform.deepseek.com/" target="_blank" rel="noreferrer">DeepSeek 官方开放平台</a>取得。安装器不会替你登录、充值或自动发起付费翻译。本地识别、校对、打轴和导出不调用翻译 API。</p><p class="muted">默认不保存密钥，重开后需再粘贴；需要时可在设置页勾选“记住密钥”，用 Windows 账户级加密保存在本机。不要把自己的密钥发给朋友或塞进安装包。API 价格以官方平台为准，不在本指南承诺固定费用。</p></section>
<section class="card"><h3>已有字幕？走这条更省事</h3><p><b>打开项目 / SRT… <span class="arrow">→</span> 选择字幕类型并看预览 <span class="arrow">→</span> 导入校对 <span class="arrow">→</span> 只补译缺少的中文</b></p><p>不要重新识别已有字幕。已翻译部分会保留；“重新翻译全部”只有在你明确要重做时才勾选。时长统一修改用 F4，可以选择当前一条或全部字幕。</p></section>
<h2>四种安装内容怎么选</h2><div class="card"><table><thead><tr><th>选择</th><th>首次基础下载</th><th>适合谁</th></tr></thead><tbody><tr><td><b>small 推荐版</b></td><td>约 0.61 GB</td><td>第一次使用、普通电脑、先把视频识别和翻译跑通</td></tr><tr><td><b>tiny 轻量版</b></td><td>约 0.2 GB</td><td>老电脑、磁盘紧张；准确度略低于 small</td></tr><tr><td>Turbo 版</td><td>约 1.75 GB</td><td>明确需要更大模型，能接受下载和资源开销</td></tr><tr><td>仅 SRT 版</td><td>约 35 MB</td><td>手头已有字幕，先翻译、校对、加字幕或打轴</td></tr></tbody></table><p class="muted">NVIDIA 加速另约 570 MB；需要时可能补约 26 MB 的微软 VC++ 运行库。下载量不包含视频素材。朋友的电脑配置未知，Turbo 不保证比 small 更适合。</p></div>
<h2>导出后找哪一个文件</h2><div class="card"><table><thead><tr><th>你想做什么</th><th>使用什么</th></tr></thead><tbody><tr><td>在 PR 中同步显示原语言字幕</td><td>原文 .srt</td></tr><tr><td>只显示中文</td><td>中文 .srt</td></tr><tr><td>原文在上、中文在下</td><td>混合 .srt</td></tr><tr><td>带样式（可调字体/颜色/位置）的中文字幕</td><td>中文 .ass</td></tr><tr><td>读稿、复制文字、审阅</td><td>对应 .txt</td></tr><tr><td>下次继续校对或翻译，避免重复工作</td><td>项目目录里的 project.json</td></tr></tbody></table><p>点击“打开输出文件夹”，在 exports 中选择最新时间的文件夹。修改项目后要重新导出，旧 SRT 不会自动更新。</p></div>
<h2 id="faq">__COUNT__ 个常见问题：直接搜报错</h2><div class="search"><input id="search" type="search" placeholder="例如：401、模型、DLL、乱码、显卡、重复翻译" aria-label="搜索问题"><select id="category" aria-label="问题分类"><option value="">全部分类</option>__OPTIONS__</select><button id="expand">展开当前问题</button><button id="collapse">收起</button></div><p id="count" class="muted" aria-live="polite"></p><div id="questions">__FAQ__</div><p id="empty" class="note" hidden>没有找到对应条目。换一个更短的关键词，或按下面的方法发诊断。</p>
<section class="card" id="support"><h3>还是不行？照这个模板发来</h3><p>① 版本：2.0；② 最后点了什么按钮；③ 看到的完整提示截图；④ 选 small 还是 Turbo、CPU 还是 GPU；⑤ 素材大约多长；⑥ 对应诊断 ZIP。</p><p><b>安装/启动失败：</b>安装器 → 导出安装诊断。<br><b>主程序处理失败：</b>诊断与资源 → 导出诊断包。</p><p class="note">截图里如果有展开的密钥，先遮住。不要发送 cookies、API 密钥、账号密码，也不用先上传整段私人视频。不要用你的旧日志代替朋友电脑刚导出的日志。</p></section>
<section class="card" id="about"><h3>作者与第三方组件</h3><p><b>本软件作者：lin1024-star（心非）</b>。免费公开给朋友使用，不收费、不出售。源码与主页：<a href="https://github.com/lin1024-star/zimu-studio" target="_blank" rel="noreferrer">github.com/lin1024-star/zimu-studio</a>。本软件自身的代码与文档采用 MIT 协议。</p><p>本软件使用了以下第三方组件与模型，<b>版权归各自作者所有</b>，在此致谢：</p><table><thead><tr><th>组件</th><th>用途</th><th>许可</th></tr></thead><tbody><tr><td><b>Kim_Vocal_2</b>（Ultimate Vocal Remover 项目）</td><td>人声分离</td><td>UVR 要求使用者署名，特此署名</td></tr><tr><td>faster-whisper、CTranslate2</td><td>语音识别</td><td>MIT</td></tr><tr><td>OpenAI Whisper 语音模型</td><td>语音识别</td><td>MIT</td></tr><tr><td>ONNX Runtime</td><td>人声分离推理</td><td>MIT</td></tr><tr><td>PyAV / FFmpeg</td><td>音视频解码</td><td>BSD / LGPL</td></tr><tr><td>Python 3.13</td><td>独立运行环境</td><td>PSF</td></tr><tr><td>NVIDIA CUDA 运行库（可选）</td><td>显卡加速</td><td>NVIDIA 许可</td></tr></tbody></table><p class="muted">人声分离模型来自 <a href="https://github.com/Anjok07/ultimatevocalremovergui" target="_blank" rel="noreferrer">Ultimate Vocal Remover (UVR)</a> 项目，开发者 Anjok07、aufr33 等；该项目的官方说明要求使用其模型的第三方开发者署名，此处署名并致谢。完整清单见包内 THIRD_PARTY.txt；软件里点标题栏右侧的版本号也能打开中文说明。</p></section>
<footer><p>本指南随 SubtitleStudio 2.0 懒人包生成于 2026-10-05。软件为原语言字幕 → 简体中文翻译工具，不是网页划词翻译器，也不提供配音或画面 OCR。</p><p>参考入口：<a href="https://api-docs.deepseek.com/zh-cn/quick_start/error_codes/">DeepSeek 错误码</a> · <a href="https://api-docs.deepseek.com/quick_start/pricing-details-cny/">DeepSeek 接入文档</a> · <a href="https://www.python.org/downloads/release/python-31315/">Python 官方运行包</a> · <a href="https://github.com/SYSTRAN/faster-whisper">faster-whisper</a>。其余步骤依据本包实际功能编写；验证范围见包内 VALIDATION.txt。</p></footer>
</main><script>
const all=[...document.querySelectorAll('.faq')],search=document.getElementById('search'),category=document.getElementById('category');function filter(){let q=search.value.trim().toLowerCase(),c=category.value,n=0;all.forEach(x=>{let show=(!c||x.dataset.category===c)&&(!q||x.textContent.toLowerCase().includes(q));x.hidden=!show;if(show)n++;});document.getElementById('count').textContent='显示 '+n+' / '+all.length+' 个问题';document.getElementById('empty').hidden=n!==0;}search.addEventListener('input',filter);category.addEventListener('change',filter);document.getElementById('expand').addEventListener('click',()=>all.filter(x=>!x.hidden).forEach(x=>x.open=true));document.getElementById('collapse').addEventListener('click',()=>all.forEach(x=>x.open=false));window.addEventListener('beforeprint',()=>all.forEach(x=>{x.hidden=false;x.open=true;}));filter();
</script></html>'''
PAGE = PAGE.replace('__COUNT__', str(len(ITEMS))).replace('__OPTIONS__', options).replace('__FAQ__', ''.join(faq))
(ROOT / 'payload/guide.html').write_text(PAGE, encoding='utf-8')
(ROOT / '小白指南.html').write_text(PAGE, encoding='utf-8')
(ROOT / '先读我_快速开始.txt').write_text(QUICK, encoding='utf-8-sig')
text = QUICK + '\n\n常见问题与解决方法（' + str(len(ITEMS)) + ' 条）\n'
for i, item in enumerate(ITEMS, 1):
    text += f'\n{i:02d}.【{item["category"]}】{item["question"]}\n{item["answer"]}\n'
(ROOT / '常见问题与解决方法.txt').write_text(text, encoding='utf-8-sig')
(ROOT / 'source/faq-data.json').write_text(json.dumps(ITEMS, ensure_ascii=False, indent=2), encoding='utf-8')
print('Generated offline guide:', len(ITEMS), 'questions in', len(groups), 'categories')
