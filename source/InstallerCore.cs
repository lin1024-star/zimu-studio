using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Globalization;
using System.IO;
using System.IO.Compression;
using System.Linq;
using System.Net;
using System.Reflection;
using System.Runtime.InteropServices;
using System.Security.Cryptography;
using System.Text;
using System.Text.RegularExpressions;
using System.Threading;

namespace SubtitleEasy {
    internal sealed class Update {public string Text;public int Percent;public Update(string text,int percent=-1){Text=text;Percent=percent;}}
    internal sealed class Spec {
        public string Name,Url,Sha;public long Size;
        public static Spec Read(Dictionary<string,object> d){return new Spec{Name=Json.Get(d,"name"),Url=Json.Get(d,"url"),Sha=Json.Get(d,"sha256"),Size=Convert.ToInt64(d["size"],CultureInfo.InvariantCulture)};}
    }
    internal sealed class Reply:IDisposable {
        public Stream Body;public int Status;public string Range;public IDisposable Response,Registration;
        public void Dispose(){if(Body!=null)Body.Dispose();if(Response!=null)Response.Dispose();if(Registration!=null)Registration.Dispose();}
    }
    internal static class Files {
        public static string Hash(string path){using(var h=SHA256.Create())using(var f=File.OpenRead(path))return BitConverter.ToString(h.ComputeHash(f)).Replace("-","").ToLowerInvariant();}
        public static string Quote(string value){var s=new StringBuilder("\"");int slash=0;foreach(char c in value){if(c=='\\'){slash++;continue;}s.Append('\\',c=='"'?slash*2+1:slash);s.Append(c);slash=0;}s.Append('\\',slash*2);return s.Append('"').ToString();}
        public static string Args(IEnumerable<string> args){return String.Join(" ",args.Select(Quote));}
        public static string Redact(string s){s=Regex.Replace(s??"",@"(?i)sk-[A-Za-z0-9_-]+","[密钥已隐藏]");s=Regex.Replace(s,@"https?://[^\s]+","[下载地址]");string user=Environment.GetFolderPath(Environment.SpecialFolder.UserProfile);if(user.Length>2)s=s.Replace(user,"%USERPROFILE%");return s;}
        public static bool Inside(string path,string root){return Path.GetFullPath(path).StartsWith(Path.GetFullPath(root).TrimEnd(new[]{Path.DirectorySeparatorChar})+Path.DirectorySeparatorChar,Environment.OSVersion.Platform==PlatformID.Win32NT?StringComparison.OrdinalIgnoreCase:StringComparison.Ordinal);}
        public static void AtomicText(string path,string text){Directory.CreateDirectory(Path.GetDirectoryName(path));string tmp=path+".writing";File.WriteAllText(tmp,text,new UTF8Encoding(false));if(File.Exists(path))File.Replace(tmp,path,null);else File.Move(tmp,path);}
        static void SafeDirectory(string path,string root){
            string relative=Path.GetFullPath(path).Substring(Path.GetFullPath(root).TrimEnd(new[]{Path.DirectorySeparatorChar}).Length).TrimStart(new[]{Path.DirectorySeparatorChar});string current=Path.GetFullPath(root);
            foreach(string part in relative.Split(new[]{Path.DirectorySeparatorChar},StringSplitOptions.RemoveEmptyEntries)){current=Path.Combine(current,part);if(Directory.Exists(current)){if((File.GetAttributes(current)&FileAttributes.ReparsePoint)!=0)throw new IOException("安装路径中存在目录链接。");}else Directory.CreateDirectory(current);}
        }
        public static Reply Open(string url,long offset,CancellationToken token){
            ServicePointManager.SecurityProtocol|=SecurityProtocolType.Tls12;
            var request=(HttpWebRequest)WebRequest.Create(url);request.Timeout=30000;request.ReadWriteTimeout=30000;request.UserAgent="SubtitleStudio-Easy/2.0";request.AllowAutoRedirect=true;request.MaximumAutomaticRedirections=12;request.Headers["Accept-Encoding"]="identity";if(offset>0)request.AddRange(offset);
            var registration=token.Register(request.Abort);
            try{var response=(HttpWebResponse)request.GetResponse();if(response.ResponseUri.Scheme!="https"){response.Dispose();throw new IOException("下载地址没有提供 HTTPS。");}return new Reply{Body=response.GetResponseStream(),Status=(int)response.StatusCode,Range=response.Headers["Content-Range"]??"",Response=response,Registration=registration};}catch{registration.Dispose();token.ThrowIfCancellationRequested();throw;}
        }
        internal static string Mirrored(string url){
            const string hf="https://huggingface.co/";
            if(!url.StartsWith(hf,StringComparison.Ordinal))return null;
            return "https://hf-mirror.com/"+url.Substring(hf.Length);
        }
        public static void Fetch(Spec spec,string destination,CancellationToken token,IProgress<Update> progress,Func<string,long,CancellationToken,Reply> open=null){
            Fetch(spec,destination,token,false,progress,open);
        }
        public static void Fetch(Spec spec,string destination,CancellationToken token,bool mirror,IProgress<Update> progress,Func<string,long,CancellationToken,Reply> open=null){
            if(spec.Size<=0||spec.Size>6L*1024*1024*1024||!Regex.IsMatch(spec.Sha??"",@"^[a-f0-9]{64}$")||!spec.Url.StartsWith("https://",StringComparison.Ordinal))throw new IOException("下载清单无效。");
            Directory.CreateDirectory(Path.GetDirectoryName(destination));token.ThrowIfCancellationRequested();
            if(File.Exists(destination)&&new FileInfo(destination).Length==spec.Size){progress.Report(new Update("校验已有文件："+spec.Name));if(Hash(destination)==spec.Sha)return;}
            string part=destination+".part";string alt=mirror?Mirrored(spec.Url):null;Exception last=null;
            string[] urls=alt==null?new[]{spec.Url}:new[]{spec.Url,alt};
            for(int attempt=0;attempt<3;attempt++){
                foreach(string url in urls){
                    token.ThrowIfCancellationRequested();if(File.Exists(part)&&new FileInfo(part).Length>spec.Size)File.Delete(part);long offset=File.Exists(part)?new FileInfo(part).Length:0;
                    if(alt!=null&&url==alt)progress.Report(new Update("切换国内镜像继续下载："+spec.Name));
                    try{
                        if(offset<spec.Size)using(var r=(open??Open)(url,offset,token)){
                            bool append=r.Status==206;
                            if(append){var match=Regex.Match(r.Range??"",@"^bytes (\d+)-(\d+)/(\d+)$");if(!match.Success||Int64.Parse(match.Groups[1].Value)!=offset||Int64.Parse(match.Groups[3].Value)!=spec.Size)throw new IOException("续传范围错误，未合并收到的数据。");}
                            else if(r.Status==200)offset=0;else throw new IOException("下载站响应异常："+r.Status);
                            long done=offset;DateTime report=DateTime.MinValue;
                            using(var f=new FileStream(part,append?FileMode.Append:FileMode.Create,FileAccess.Write,FileShare.Read)){byte[] b=new byte[262144];int n;while((n=r.Body.Read(b,0,b.Length))>0){token.ThrowIfCancellationRequested();done+=n;if(done>spec.Size)throw new IOException("下载文件大小异常。");f.Write(b,0,n);if((DateTime.UtcNow-report).TotalMilliseconds>200){progress.Report(new Update(spec.Name+"："+(done/1000000.0).ToString("0.0")+" / "+(spec.Size/1000000.0).ToString("0.0")+" MB",(int)(100*done/spec.Size)));report=DateTime.UtcNow;}}}
                        }
                        token.ThrowIfCancellationRequested();progress.Report(new Update("校验下载文件："+spec.Name));
                        if(!File.Exists(part)||new FileInfo(part).Length!=spec.Size)throw new IOException("下载尚未完整，下次重试会尝试续传。");
                        if(Hash(part)!=spec.Sha){File.Delete(part);throw new IOException("文件校验不通过，已丢弃错误片段，请重试。");}
                        if(File.Exists(destination))File.Replace(part,destination,null);else File.Move(part,destination);return;
                    }catch(OperationCanceledException){throw;}
                    catch(Exception ex){token.ThrowIfCancellationRequested();last=ex;if(ex is WebException){var wr=((WebException)ex).Response;if(wr!=null)wr.Dispose();}if(!(ex is IOException)&&!(ex is WebException))throw;}
                }
                if(attempt<2){progress.Report(new Update("连接中断，准备重试（"+(attempt+2)+"/3）："+spec.Name));if(token.WaitHandle.WaitOne(900))token.ThrowIfCancellationRequested();}
            }
            throw new IOException("下载失败："+spec.Name+"。请检查网络后点同一个安装按钮重试；已完成的文件和可用片段会复用。",last);
        }
        public static void FetchAll(List<Spec> specs,string directory,CancellationToken token,bool mirror,IProgress<Update> progress,Func<string,long,CancellationToken,Reply> open=null){
            if(specs.Count==0)return;
            ServicePointManager.DefaultConnectionLimit=6;
            progress.Report(new Update("开始并行下载 "+specs.Count+" 个文件…"));
            var pending=new Queue<Spec>(specs);Exception failure=null;object gate=new object();
            int workers=Math.Min(3,specs.Count);var threads=new List<Thread>(workers);
            for(int i=0;i<workers;i++){
                var t=new Thread(()=>{
                    while(true){
                        Spec spec=null;
                        lock(gate){if(failure!=null||pending.Count==0)return;spec=pending.Dequeue();}
                        try{Fetch(spec,Path.Combine(directory,spec.Name),token,mirror,progress,open);}
                        catch(Exception ex){lock(gate){if(failure==null)failure=ex;}}
                    }
                }){IsBackground=true};t.Start();threads.Add(t);
            }
            foreach(var t in threads)t.Join();
            if(failure!=null){if(failure is OperationCanceledException)throw (OperationCanceledException)failure;throw new IOException("并行下载失败："+failure.Message,failure);}
        }
        public static void Extract(string zip,string root,CancellationToken token){
            Directory.CreateDirectory(root);if((File.GetAttributes(root)&FileAttributes.ReparsePoint)!=0)throw new IOException("安装目录不能是目录链接。");long total=0;
            using(var archive=ZipFile.OpenRead(zip))foreach(var e in archive.Entries){
                token.ThrowIfCancellationRequested();string name=e.FullName.Replace('\\','/');if(String.IsNullOrEmpty(name))continue;
                if(name.StartsWith("/")||name.Contains(":")||name.Split(new[]{'/'},StringSplitOptions.None).Any(p=>p==".."))throw new IOException("压缩包包含不安全的路径。");
                string dst=Path.GetFullPath(Path.Combine(root,name.Replace('/',Path.DirectorySeparatorChar)));if(!Inside(dst,root))throw new IOException("压缩包路径超出了安装目录。");
                total+=e.Length;if(e.Length>1024L*1024*1024||total>3L*1024*1024*1024)throw new IOException("压缩包内容过大。");
                string parent=Path.GetDirectoryName(dst);SafeDirectory(parent,root);
                if(name.EndsWith("/")){SafeDirectory(dst,root);continue;}
                if(File.Exists(dst)&&(File.GetAttributes(dst)&FileAttributes.ReparsePoint)!=0)throw new IOException("安装文件不能是链接。");
                string tmp=dst+".unpacking";if(File.Exists(tmp)&&(File.GetAttributes(tmp)&FileAttributes.ReparsePoint)!=0)throw new IOException("解压临时文件不能是链接。");try{using(var input=e.Open())using(var output=File.Create(tmp)){byte[] b=new byte[131072];int n;while((n=input.Read(b,0,b.Length))>0){token.ThrowIfCancellationRequested();output.Write(b,0,n);}}if(File.Exists(dst))File.Replace(tmp,dst,null);else File.Move(tmp,dst);}finally{if(File.Exists(tmp))File.Delete(tmp);}
            }
        }
    }
    internal sealed class ProcessResult {public int ExitCode;public string Output;}
    internal static class Processes {
        public static ProcessResult Run(string executable,IEnumerable<string> args,string directory,CancellationToken token,Action<string> log){
            token.ThrowIfCancellationRequested();var output=new StringBuilder();object gate=new object();
            var start=new ProcessStartInfo(executable,Files.Args(args)){UseShellExecute=false,CreateNoWindow=true,WorkingDirectory=directory,RedirectStandardOutput=true,RedirectStandardError=true,RedirectStandardInput=true,StandardOutputEncoding=Encoding.UTF8,StandardErrorEncoding=Encoding.UTF8};
            start.EnvironmentVariables["HF_HUB_DISABLE_TELEMETRY"]="1";start.EnvironmentVariables["PYTHONUTF8"]="1";
            using(var p=new Process{StartInfo=start}){
                DataReceivedEventHandler read=(s,e)=>{if(e.Data==null)return;lock(gate){if(output.Length>60000)output.Remove(0,30000);output.AppendLine(Files.Redact(e.Data));}if(log!=null)try{log(e.Data);}catch{}};
                p.OutputDataReceived+=read;p.ErrorDataReceived+=read;p.Start();p.StandardInput.Close();p.BeginOutputReadLine();p.BeginErrorReadLine();
                using(token.Register(()=>{try{if(!p.HasExited){if(Environment.OSVersion.Platform==PlatformID.Win32NT){using(var k=Process.Start(new ProcessStartInfo(Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.System),"taskkill.exe"),"/PID "+p.Id+" /T /F"){CreateNoWindow=true,UseShellExecute=false})){if(k!=null)k.WaitForExit(4000);}}if(!p.HasExited)p.Kill();}}catch{}})){p.WaitForExit();}
                token.ThrowIfCancellationRequested();return new ProcessResult{ExitCode=p.ExitCode,Output=output.ToString()};
            }
        }
    }
    internal static class Installer {
        public const string Version="2.1";
        public const string RegistryKey="Software\\SubtitleStudio";
        public static string DefaultRoot {get{return Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),"SubtitleStudio");}}
        public static string ConfiguredRoot {
            get {
                try{using(var key=Microsoft.Win32.Registry.CurrentUser.OpenSubKey(RegistryKey)){if(key!=null){string v=Convert.ToString(key.GetValue("Root")??"");if(!String.IsNullOrEmpty(v)&&Path.IsPathRooted(v))return Path.GetFullPath(v);}}}catch{}
                return DefaultRoot;
            }
            set {Directory.CreateDirectory(value);using(var key=Microsoft.Win32.Registry.CurrentUser.CreateSubKey(RegistryKey)){if(key!=null)key.SetValue("Root",Path.GetFullPath(value));}}
        }
        public static string DataRoot {
            get {
                // 装好的启动器就在 <根目录>\Easy\SubtitleStudio.exe：按自身位置定位最可靠，
                // 这样整个安装可以放在任意盘符，不依赖注册表。分发包从别处运行时才回退到注册表 / 默认值。
                try{
                    string dir=Path.GetDirectoryName(Path.GetFullPath(Assembly.GetExecutingAssembly().Location));
                    if(!String.IsNullOrEmpty(dir)&&String.Equals(Path.GetFileName(dir),"Easy",StringComparison.OrdinalIgnoreCase)){
                        string parent=Path.GetDirectoryName(dir);
                        if(!String.IsNullOrEmpty(parent))return parent;
                    }
                }catch{}
                return ConfiguredRoot;
            }
        }
        public static string Root {get{return Path.Combine(DataRoot,"Easy");}}
        public static string App {get{return Path.Combine(Root,"app-"+Version);}}
        public static string PythonRoot {get{return Path.Combine(Root,"python-3.13.15");}}
        public static string Python {get{return Path.Combine(PythonRoot,"python.exe");}}
        public static string Cache {get{return Path.Combine(Root,"cache");}}
        public static string Ready {get{return Path.Combine(Root,"ready-"+Version+".json");}}
        public static string LogFile {get{return Path.Combine(Root,"install.log");}}
        public static Dictionary<string,object> Manifest(){using(var s=Assembly.GetExecutingAssembly().GetManifestResourceStream("manifest.json"))using(var r=new StreamReader(s,Encoding.UTF8))return Json.Object(r.ReadToEnd());}
        public static void Payload(CancellationToken token){Directory.CreateDirectory(Cache);string zip=Path.Combine(Cache,"app-"+Version+".zip");using(var input=Assembly.GetExecutingAssembly().GetManifestResourceStream("payload.zip"))using(var output=File.Create(zip))input.CopyTo(output);Files.Extract(zip,App,token);}
        static List<string> Script(string action,params string[] extra){var list=new List<string>{"-X","utf8","-E","-s",Path.Combine(App,"easy_runtime.py"),action};list.AddRange(extra);return list;}
        static bool Probe(string mode,CancellationToken token,Action<string> log){if(!File.Exists(Python))return false;try{using(var limited=CancellationTokenSource.CreateLinkedTokenSource(token)){limited.CancelAfter(60000);return Processes.Run(Python,Script("probe-"+mode),App,limited.Token,log).ExitCode==0;}}catch(OperationCanceledException){token.ThrowIfCancellationRequested();return false;}catch{return false;}}
        public static bool HasNvidiaGpu(){
            try{
                using(var s=new System.Management.ManagementObjectSearcher("SELECT Name FROM Win32_VideoController")){
                    foreach(var o in s.Get()){using(o){var n=Convert.ToString(o["Name"]??"");if(n.IndexOf("NVIDIA",StringComparison.OrdinalIgnoreCase)>=0)return true;}}
                }
            }catch{}
            return false;
        }
        static void EnsureVc(Dictionary<string,object> manifest,bool mirror,CancellationToken token,IProgress<Update> progress,Action<string> log){
            var spec=Spec.Read((Dictionary<string,object>)manifest["vc_runtime"]);string exe=Path.Combine(Cache,spec.Name);Files.Fetch(spec,exe,token,mirror,progress);
            progress.Report(new Update("需要补齐 Windows 运行库。如果系统弹出权限窗口，请确认发布者为 Microsoft 后选择“是”。"));log("正在运行 Microsoft VC++ 安装器；不更改杀毒设置。");
            token.ThrowIfCancellationRequested();using(var p=Process.Start(new ProcessStartInfo(exe,"/install /passive /norestart"){UseShellExecute=true,Verb="runas"})){if(p==null)throw new IOException("Windows 运行库安装器未启动。");p.WaitForExit();if(p.ExitCode!=0&&p.ExitCode!=3010&&p.ExitCode!=1638)throw new IOException("Windows 运行库未安装完成，返回码 "+p.ExitCode+"。请看小白指南的 DLL 错误一节。");if(p.ExitCode==3010)log("Windows 运行库建议重启电脑。若下一步仍提示 DLL，请重启后再次点安装按钮。");}token.ThrowIfCancellationRequested();
        }
        // 「主程序是否在运行」原先只看能不能独占打开 application.lock。实测这个判据不可靠：
        // 有使用者遇到软件已经干净退出（诊断日志里 session_closed、closed_cleanly 都是 true），
        // 安装器却在三分钟后报 UnauthorizedAccessException 访问被拒绝，重试三次都一样；
        // 而同一时刻用完全相同的代码手动打开却成功，文件属性正常、也没有拒绝 ACE。
        // 原因始终没能复现，所以不再依赖它：换成两条各自独立、都不看共享模式的检查。
        //   1) 有没有进程真的从安装目录里跑着——最直接；
        //   2) 主程序自己那把字节范围锁还在不在——start_app.py 用 msvcrt.locking 加锁，
        //      这里试着加同一把锁，加得上就说明没人在跑。与主程序自己的判定方式一致。
        // 文件打不开本身不再当成"在运行"：宁可放行也不要让使用者卡在一个无法解释的错误上，
        // 而进程检查已经能覆盖真正"还在跑"的情况。
        static bool ProcessRunningFrom(string folder,Action<string> log){
            try{
                string prefix=Path.GetFullPath(folder).TrimEnd('\\')+"\\";
                foreach(var process in Process.GetProcesses()){
                    try{
                        string file=process.MainModule.FileName;
                        if(!String.IsNullOrEmpty(file)&&file.StartsWith(prefix,StringComparison.OrdinalIgnoreCase)){
                            Note(log,"发现正在运行的进程："+file);
                            return true;
                        }
                    }catch{}finally{process.Dispose();}
                }
            }catch(Exception e){Note(log,"进程检查跳过："+e.GetType().Name);}
            return false;
        }
        internal static void EnsureAppClosed(string appLock,string appFolder,CancellationToken token,Action<string> log){
            token.ThrowIfCancellationRequested();
            bool busy=ProcessRunningFrom(appFolder,log);
            if(!busy){
                if(!File.Exists(appLock)){Note(log,"未发现 application.lock，跳过锁检查。");}
                else{
                    try{
                        using(var stream=new FileStream(appLock,FileMode.Open,FileAccess.ReadWrite,FileShare.ReadWrite)){
                            try{stream.Lock(0,1);stream.Unlock(0,1);Note(log,"application.lock 的锁是空的，字幕工坊未在运行。");}
                            catch(IOException){busy=true;Note(log,"application.lock 上还挂着一把锁，字幕工坊仍在运行。");}
                        }
                    }catch(IOException e){Note(log,"application.lock 打不开（"+e.GetType().Name+"），按未运行处理："+e.Message);}
                    catch(UnauthorizedAccessException e){Note(log,"application.lock 打不开（"+e.GetType().Name+"），按未运行处理："+e.Message);}
                }
            }
            if(busy)throw new IOException("字幕工坊仍在运行。请保存项目并完全关闭字幕工坊窗口："
                +"若当时有识别 / 翻译在跑，关窗口会先问“停止当前任务并退出”，"
                +"要等任务结束、窗口消失之后进程才真正退出。隔十几秒再点安装 / 修复。");
        }
        [DllImport("advapi32.dll",SetLastError=true)]static extern bool OpenProcessToken(IntPtr process,int access,out IntPtr token);
        [DllImport("advapi32.dll",SetLastError=true)]static extern bool GetTokenInformation(IntPtr token,int infoClass,IntPtr info,int len,out int needed);
        [DllImport("kernel32.dll")]static extern IntPtr GetCurrentProcess();
        [DllImport("kernel32.dll")]static extern bool CloseHandle(IntPtr handle);
        // 本进程的完整性级别。使用者遇到的那次"访问被拒绝"就是它造成的：Windows 会按可执行文件所在
        // 文件夹的标签降低进程完整性，低完整性进程只能读、不能写更高完整性的目录。把它记进日志，
        // 下次一眼就能看出是权限级别问题，而不是又去猜共享模式。
        internal static string IntegrityLevel(){
            IntPtr token=IntPtr.Zero,buffer=IntPtr.Zero;
            try{
                if(!OpenProcessToken(GetCurrentProcess(),0x0008,out token))return "未知";
                int needed;
                GetTokenInformation(token,25,IntPtr.Zero,0,out needed);   // TokenIntegrityLevel
                if(needed<=0)return "未知";
                buffer=Marshal.AllocHGlobal(needed);
                if(!GetTokenInformation(token,25,buffer,needed,out needed))return "未知";
                IntPtr sid=Marshal.ReadIntPtr(buffer);                    // TOKEN_MANDATORY_LABEL.Label.Sid
                int count=Marshal.ReadByte(sid,1);                        // 子作者数量
                int rid=Marshal.ReadInt32(sid,8+(count-1)*4);             // 最后一个子作者
                if(rid>=0x4000)return "系统";
                if(rid>=0x3000)return "高（以管理员运行）";
                if(rid>=0x2000)return "中（普通）";
                return "低（受限）";
            }catch{return "未知";}finally{
                if(buffer!=IntPtr.Zero)Marshal.FreeHGlobal(buffer);
                if(token!=IntPtr.Zero)CloseHandle(token);
            }
        }
        // 安装前先确认真的写得进去。写不进去时给出能照做的说明：使用者看到的不该是一句
        // "UnauthorizedAccessException：访问被拒绝"，而该是"把 ZIP 解压到桌面再运行"。
        internal static void EnsureWritable(string folder,Action<string> log){
            string level=IntegrityLevel();
            try{
                Directory.CreateDirectory(folder);
                string probe=Path.Combine(folder,".write-test-"+Guid.NewGuid().ToString("N"));
                using(var writer=new StreamWriter(probe,false,new UTF8Encoding(false)))writer.Write("ok");
                File.Delete(probe);
                Note(log,"安装目录可写；本程序完整性级别："+level+"。");
            }catch(UnauthorizedAccessException e){
                Note(log,"写入被拒绝；本程序完整性级别："+level+"；目标："+folder+"；"+e.Message);
                throw new IOException("安装程序没有写入权限：" + folder + "\r\n\r\n"
                    +"最常见的原因：安装包被解压到了一个「受限文件夹」里。Windows 会给某些文件夹标上"
                    +"「低完整性」，从这类文件夹里运行的程序只能读、不能写。\r\n\r\n"
                    +"解决办法：把下载的 ZIP 解压到「桌面」，再从桌面的文件夹里双击安装程序。\r\n"
                    +"（当前安装程序运行在「"+level+"」完整性级别。）",e);
            }catch(IOException e){
                Note(log,"写测试失败（"+e.GetType().Name+"）："+e.Message);
                throw new IOException("安装程序无法写入：" + folder + "\r\n\r\n"+e.Message,e);
            }
        }
        static void Note(Action<string> log,string message){if(log!=null){try{log(message);}catch{}}}
        public static void Install(string model,bool gpu,bool mirror,CancellationToken token,IProgress<Update> progress,Action<string> log){
            if(!new[]{"small","turbo","tiny","srt"}.Contains(model))throw new ArgumentException("安装模式无效。");
            token.ThrowIfCancellationRequested();EnsureAppClosed(Path.Combine(DataRoot,"application.lock"),Root,token,log);
            EnsureWritable(Cache,log);
            Directory.CreateDirectory(Root);var drive=new DriveInfo(Path.GetPathRoot(Root));long need=model=="turbo"?7L*1000000000:model=="small"?3L*1000000000:model=="tiny"?15L*100000000:500L*1000000;
            if(drive.AvailableFreeSpace<need)throw new IOException("安装所在磁盘空间不足。此模式请预留约 "+(need/1000000000.0).ToString("0.0")+" GB 后重试；已有 SRT 可先选择仅字幕模式。");
            if(File.Exists(Ready))File.Delete(Ready); // A cancelled repair must not remain marked as complete.
            progress.Report(new Update("1/6　准备字幕工坊程序…"));Payload(token);var manifest=Manifest();
            progress.Report(new Update("2/6　准备独立 Python 运行环境…"));
            if(!Probe("basic",token,null)){
                var spec=Spec.Read((Dictionary<string,object>)manifest["python"]);string zip=Path.Combine(Cache,spec.Name);Files.Fetch(spec,zip,token,mirror,progress);progress.Report(new Update("2/6　展开运行环境，请稍等…"));Files.Extract(zip,PythonRoot,token);
                if(!Probe("basic",token,log))throw new IOException("E102：Python / 窗口组件未能启动。请关闭旧窗口后重试；仍失败时导出安装诊断。");
            }
            if(model!="srt"){
                progress.Report(new Update("3/6　准备语音识别组件…"));
                if(!Probe("asr",token,null)){
                    string wheelDir=Path.Combine(Cache,"wheels");var wheels=new List<Spec>();foreach(var item in (List<object>)manifest["wheels"])wheels.Add(Spec.Read((Dictionary<string,object>)item));Files.FetchAll(wheels,wheelDir,token,mirror,progress);
                    progress.Report(new Update("3/6　安装已校验的识别组件…"));
                    var args=new[]{"-X","utf8","-I","-m","pip","--isolated","install","--no-index","--find-links",wheelDir,"--require-hashes","--only-binary=:all:","--force-reinstall","--disable-pip-version-check","--no-warn-script-location","-r",Path.Combine(App,"requirements-windows.lock")};
                    if(Processes.Run(Python,args,App,token,log).ExitCode!=0)throw new IOException("E201：识别组件安装未完成。请关闭字幕工坊，再点安装 / 修复；若仍失败，请导出安装诊断。");
                    if(!Probe("asr",token,log)){EnsureVc(manifest,mirror,token,progress,log);if(!Probe("asr",token,log))throw new IOException("E202：识别组件仍无法载入。请重启电脑后重试；旧电脑 CPU 不兼容或 DLL 被安全软件拦截时，请查看指南并导出诊断。");}
                }
                progress.Report(new Update("4/6　准备 "+model+" 识别模型…"));var models=(Dictionary<string,object>)manifest["models"];var modelSpec=(Dictionary<string,object>)models[model];string modelRoot=Path.Combine(DataRoot,"models","prepared",model);Directory.CreateDirectory(modelRoot);
                var modelFiles=new List<Spec>();foreach(var item in (List<object>)modelSpec["files"])modelFiles.Add(Spec.Read((Dictionary<string,object>)item));Files.FetchAll(modelFiles,modelRoot,token,mirror,progress);
                Files.AtomicText(Path.Combine(modelRoot,"ready.json"),Json.Write(modelSpec));
            }
            bool gpuReady=false;progress.Report(new Update("5/6　完成运行检查…"));
            if(gpu&&model!="srt"){
                progress.Report(new Update("5/6　准备可选 NVIDIA 加速；不可用时保留 CPU 模式…"));
                var result=Processes.Run(Python,Script("gpu"),App,token,log);gpuReady=result.ExitCode==0;if(!gpuReady)log("NVIDIA 加速本次未准备成功。CPU 模式已可用，之后可在设置中再次启用显卡加速。");
            }
            var finalize=Script("finalize","--model",model);if(gpuReady)finalize.Add("--gpu");if(Processes.Run(Python,finalize,App,token,log).ExitCode!=0)throw new IOException("E401：无法写入首次设置，请检查用户文件夹权限。");
            progress.Report(new Update("6/6　建立桌面快捷方式…"));InstallLauncher();try{Shortcut();}catch{log("桌面快捷方式未能创建；仍可通过本安装程序的“启动字幕工坊”按钮打开。");}
            Files.AtomicText(Ready,Json.Write(new Dictionary<string,object>{{"version",Version},{"mode",model},{"gpu_ready",gpuReady},{"date",DateTime.UtcNow.ToString("o")}}));progress.Report(new Update("安装完成。可以启动字幕工坊。",100));
        }
        static void InstallLauncher(){string source=Assembly.GetExecutingAssembly().Location,dest=Path.Combine(Root,"SubtitleStudio.exe");if(Path.GetFullPath(source).Equals(Path.GetFullPath(dest),StringComparison.OrdinalIgnoreCase))return;string tmp=dest+".new";File.Copy(source,tmp,true);if(File.Exists(dest))File.Replace(tmp,dest,null);else File.Move(tmp,dest);}
        static void Set(object target,string name,object value){target.GetType().InvokeMember(name,BindingFlags.SetProperty,null,target,new[]{value});}
        static void Shortcut(){
            string desktop=Environment.GetFolderPath(Environment.SpecialFolder.DesktopDirectory);Directory.CreateDirectory(desktop);object shell=null,link=null,shell2=null,link2=null;
            string exe=Path.Combine(Root,"SubtitleStudio.exe");string live=Path.Combine(App,"live","app.py");
            try{
                shell=Activator.CreateInstance(Type.GetTypeFromProgID("WScript.Shell"));link=shell.GetType().InvokeMember("CreateShortcut",BindingFlags.InvokeMethod,null,shell,new[]{Path.Combine(desktop,"字幕工坊.lnk")});Set(link,"TargetPath",exe);Set(link,"Arguments","--launch");Set(link,"WorkingDirectory",Root);Set(link,"Description","字幕工坊：本地识别、字幕翻译与校对");link.GetType().InvokeMember("Save",BindingFlags.InvokeMethod,null,link,null);
                // 实时字幕是独立小工具，单独一个快捷方式，不塞进主界面（两者的使用流程完全不同）。
                // 用 pythonw 启动：不弹黑窗口；出错时程序自己弹提示框并写日志。
                if(File.Exists(live)){
                    shell2=Activator.CreateInstance(Type.GetTypeFromProgID("WScript.Shell"));link2=shell2.GetType().InvokeMember("CreateShortcut",BindingFlags.InvokeMethod,null,shell2,new[]{Path.Combine(desktop,"实时中文字幕.lnk")});Set(link2,"TargetPath",Path.Combine(PythonRoot,"pythonw.exe"));Set(link2,"Arguments",Files.Args(new[]{"-X","utf8","-E","-s",live}));Set(link2,"WorkingDirectory",App);Set(link2,"Description","实时中文字幕：看直播时把日语实时翻成中文字幕");link2.GetType().InvokeMember("Save",BindingFlags.InvokeMethod,null,link2,null);
                }
            }
            catch{File.WriteAllText(Path.Combine(desktop,"字幕工坊.cmd"),"@echo off\r\nstart \"\" \""+exe+"\" --launch\r\n",Encoding.ASCII);}
            finally{if(link2!=null)Marshal.FinalReleaseComObject(link2);if(shell2!=null)Marshal.FinalReleaseComObject(shell2);if(link!=null)Marshal.FinalReleaseComObject(link);if(shell!=null)Marshal.FinalReleaseComObject(shell);}
        }
        public static bool Installed {get{return File.Exists(Ready)&&File.Exists(Python)&&File.Exists(Path.Combine(App,"start_app.py"));}}
        public static void Launch(){if(!Installed)throw new IOException("尚未完成安装，请先点“一键安装 / 修复”。");var psi=new ProcessStartInfo(Path.Combine(PythonRoot,"pythonw.exe"),Files.Args(new[]{"-X","utf8","-E","-s",Path.Combine(App,"start_app.py")})){UseShellExecute=false,CreateNoWindow=true,WorkingDirectory=App};using(var p=Process.Start(psi)){if(p==null)throw new IOException("软件没有启动。");if(p.WaitForExit(1800)&&p.ExitCode!=0)throw new IOException("启动失败。请点“导出安装诊断”，或重新安装 / 修复。");}}
        public static void Guide(){string file=Path.Combine(App,"guide.html");if(!File.Exists(file))Payload(CancellationToken.None);foreach(string env in new[]{"ProgramFiles(x86)","ProgramFiles","LOCALAPPDATA"}){string root=Environment.GetEnvironmentVariable(env);if(String.IsNullOrEmpty(root))continue;string edge=Path.Combine(root,"Microsoft","Edge","Application","msedge.exe");if(File.Exists(edge)){Process.Start(new ProcessStartInfo(edge,Files.Quote(file)){UseShellExecute=false});return;}}Process.Start(new ProcessStartInfo(file){UseShellExecute=true});}
        public static void ExportDiagnostics(string file){
            Directory.CreateDirectory(Root);var report=new StringBuilder("SubtitleStudio Easy "+Version+"\n");report.AppendLine("OS: "+Environment.OSVersion+"; 64-bit: "+Environment.Is64BitOperatingSystem);report.AppendLine("Runtime exists: "+File.Exists(Python)+"; install marker: "+File.Exists(Ready));
            if(File.Exists(Python)&&Directory.Exists(App)){try{using(var token=new CancellationTokenSource(20000)){report.AppendLine(Processes.Run(Python,Script("diagnose"),App,token.Token,null).Output);}}catch(Exception ex){report.AppendLine("Diagnostic probe: "+ex.GetType().Name);}}
            using(var z=ZipFile.Open(file,ZipArchiveMode.Create)){
                using(var w=new StreamWriter(z.CreateEntry("environment.txt").Open(),new UTF8Encoding(false)))w.Write(report.ToString());
                foreach(var item in new[]{new[]{LogFile,"install.log"},new[]{Path.Combine(DataRoot,"startup-error.txt"),"startup-error.txt"}})if(File.Exists(item[0]))using(var w=new StreamWriter(z.CreateEntry(item[1]).Open(),new UTF8Encoding(false)))w.Write(Files.Redact(File.ReadAllText(item[0],Encoding.UTF8)));
            }
        }
    }
}
