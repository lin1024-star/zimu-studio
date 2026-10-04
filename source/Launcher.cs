using System;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Linq;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using System.Windows.Forms;

namespace SubtitleEasy {
    internal sealed class SetupForm:Form {
        readonly ComboBox mode=new ComboBox();readonly CheckBox gpu=new CheckBox(),autoStart=new CheckBox(),mirror=new CheckBox();
        readonly Button install=new Button(),launch=new Button(),cancel=new Button(),export=new Button();
        readonly Label status=new Label(),description=new Label();readonly ProgressBar progress=new ProgressBar();readonly TextBox logs=new TextBox();
        CancellationTokenSource cancellation;bool busy,closeAfter;
        public SetupForm(){
            Text="字幕工坊 · 懒人安装与启动 1.6.1";Font=new Font("Microsoft YaHei UI",9F);ClientSize=new Size(890,690);MinimumSize=new Size(850,650);StartPosition=FormStartPosition.CenterScreen;BackColor=Color.FromArgb(243,247,249);AutoScaleMode=AutoScaleMode.Dpi;
            var root=new TableLayoutPanel{Dock=DockStyle.Fill,Padding=new Padding(24,20,24,18),ColumnCount=1,RowCount=9};Controls.Add(root);
            foreach(int h in new[]{42,40,135,45,58,32})root.RowStyles.Add(new RowStyle(SizeType.Absolute,h));root.RowStyles.Add(new RowStyle(SizeType.Percent,100));root.RowStyles.Add(new RowStyle(SizeType.Absolute,46));root.RowStyles.Add(new RowStyle(SizeType.Absolute,35));
            root.Controls.Add(new Label{Text="字幕工坊 · 一键准备",AutoSize=true,Font=new Font(Font.FontFamily,21,FontStyle.Bold),ForeColor=Color.FromArgb(21,61,70)},0,0);
            root.Controls.Add(new Label{Text="第一次用：选推荐设置 → 点一键安装 → 等待完成。之后双击桌面的“字幕工坊”。",AutoSize=true,ForeColor=Color.FromArgb(81,102,118),Margin=new Padding(0,5,0,0)},0,1);
            var options=new TableLayoutPanel{Dock=DockStyle.Fill,BackColor=Color.White,Padding=new Padding(12),ColumnCount=2,RowCount=4,Margin=new Padding(0,0,0,12)};options.ColumnStyles.Add(new ColumnStyle(SizeType.Absolute,89));options.ColumnStyles.Add(new ColumnStyle(SizeType.Percent,100));options.RowStyles.Add(new RowStyle(SizeType.Absolute,36));options.RowStyles.Add(new RowStyle(SizeType.Absolute,33));options.RowStyles.Add(new RowStyle(SizeType.Absolute,33));options.RowStyles.Add(new RowStyle(SizeType.Percent,100));root.Controls.Add(options,0,2);
            options.Controls.Add(new Label{Text="安装内容",AutoSize=true,Margin=new Padding(0,8,0,0)},0,0);mode.DropDownStyle=ComboBoxStyle.DropDownList;mode.Items.AddRange(new object[]{"推荐：完整安装 + small 模型（首次约 0.61 GB）","可选：完整安装 + Turbo 模型（首次约 1.75 GB）","轻量：完整安装 + tiny 模型（首次约 0.2 GB，适合老电脑）","轻量：只翻译 / 校对已有 SRT（首次约 35 MB）"});mode.Dock=DockStyle.Fill;mode.Margin=new Padding(0,4,0,4);mode.SelectedIndex=0;mode.SelectedIndexChanged+=(s,e)=>RefreshMode();options.Controls.Add(mode,1,0);
            gpu.Text="同时准备 NVIDIA 显卡加速（可选，另约 570 MB；需要 NVIDIA 显卡）";gpu.AutoSize=true;gpu.Margin=new Padding(0,5,0,0);options.Controls.Add(gpu,1,1);mirror.Text="网络不佳时使用国内镜像（hf-mirror.com，仅加速模型下载）";mirror.AutoSize=true;mirror.Checked=true;mirror.Margin=new Padding(0,5,0,0);options.Controls.Add(mirror,1,2);description.AutoSize=true;description.Dock=DockStyle.Fill;description.ForeColor=Color.FromArgb(96,109,121);description.Margin=new Padding(0,4,0,0);options.Controls.Add(description,0,3);options.SetColumnSpan(description,2);
            var actions=new FlowLayoutPanel{Dock=DockStyle.Fill,WrapContents=false,Margin=new Padding(0)};root.Controls.Add(actions,0,3);SetupButton(install,"一键安装 / 修复",160,InstallClick);install.BackColor=Color.FromArgb(20,122,130);install.ForeColor=Color.White;install.FlatStyle=FlatStyle.Flat;actions.Controls.Add(install);SetupButton(launch,"启动字幕工坊",145,LaunchClick);actions.Controls.Add(launch);SetupButton(cancel,"取消当前任务",135,(s,e)=>{if(cancellation!=null){status.Text="正在结束当前操作，请稍等…";cancel.Enabled=false;cancellation.Cancel();}});cancel.Enabled=false;actions.Controls.Add(cancel);autoStart.Text="安装完成后自动打开";autoStart.AutoSize=true;autoStart.Checked=true;autoStart.Margin=new Padding(8,9,0,0);actions.Controls.Add(autoStart);
            var line=new TableLayoutPanel{Dock=DockStyle.Fill,ColumnCount=1,RowCount=2,Margin=new Padding(0)};line.RowStyles.Add(new RowStyle(SizeType.Absolute,33));line.RowStyles.Add(new RowStyle(SizeType.Absolute,17));root.Controls.Add(line,0,4);status.Dock=DockStyle.Fill;status.TextAlign=ContentAlignment.MiddleLeft;status.AutoEllipsis=true;status.Margin=new Padding(0);line.Controls.Add(status,0,0);progress.Dock=DockStyle.Fill;progress.Margin=new Padding(0);line.Controls.Add(progress,0,1);
            root.Controls.Add(new Label{Text="当前文件进度达到 100% 后，程序会继续安装后续内容。大模型校验时请耐心等待。",AutoSize=true,ForeColor=Color.FromArgb(96,109,121),Margin=new Padding(0,6,0,0)},0,5);
            logs.Multiline=true;logs.ReadOnly=true;logs.ScrollBars=ScrollBars.Vertical;logs.Dock=DockStyle.Fill;logs.BackColor=Color.White;logs.Margin=new Padding(0);root.Controls.Add(logs,0,6);
            var support=new FlowLayoutPanel{Dock=DockStyle.Fill,WrapContents=false,Margin=new Padding(0,10,0,0)};root.Controls.Add(support,0,7);support.Controls.Add(NewButton("小白指南 / 常见问题",182,(s,e)=>{try{Installer.Guide();}catch(Exception ex){Error(ex.Message);}}));SetupButton(export,"导出安装诊断",145,ExportClick);support.Controls.Add(export);support.Controls.Add(NewButton("打开安装目录",142,(s,e)=>{Directory.CreateDirectory(Installer.Root);Process.Start(new ProcessStartInfo(Installer.Root){UseShellExecute=true});}));support.Controls.Add(NewButton("DeepSeek 官方平台",178,(s,e)=>Process.Start(new ProcessStartInfo("https://platform.deepseek.com/"){UseShellExecute=true})));
            root.Controls.Add(new Label{Text="安装不需要 API 密钥。翻译时再填朋友自己的密钥，费用由其 API 账户结算；识别与校对不调用翻译 API。",AutoSize=true,ForeColor=Color.FromArgb(80,99,112),Margin=new Padding(0,9,0,0)},0,8);
            if(Installer.HasNvidiaGpu()){gpu.Checked=true;Append("检测到 NVIDIA 显卡，已自动勾选“显卡加速”；不需要可以取消。");}
            RefreshMode();RefreshInstalled();Append("字幕工坊懒人版 1.6.1；完整程序，保留 SRT 导入、校对、加字幕、打轴和六份导出。");FormClosing+=ClosingForm;
        }
        static void SetupButton(Button b,string text,int width,EventHandler action){b.Text=text;b.Width=width;b.Height=34;b.Margin=new Padding(0,0,9,0);b.Click+=action;}
        static Button NewButton(string text,int width,EventHandler action){var b=new Button();SetupButton(b,text,width,action);return b;}
        void RefreshMode(){if(mode.SelectedIndex==3){gpu.Checked=false;gpu.Enabled=false;mirror.Enabled=false;description.Text="仅 SRT 模式不下载语音识别模型。以后要处理视频，再用本安装器补装即可。";}else if(mode.SelectedIndex==2){gpu.Enabled=!busy;mirror.Enabled=!busy;description.Text="tiny 模型约 0.2 GB，识别速度要求低，适合老电脑或磁盘紧张；准确度略低于 small。";}else{gpu.Enabled=!busy;mirror.Enabled=!busy;description.Text=mode.SelectedIndex==1?"Turbo 较大，请预留约 7 GB 磁盘空间；普通电脑优先用 small 试短视频。":"默认 CPU + small，不要求显卡或 CUDA。请预留约 3 GB 磁盘空间，首次安装需要联网。";}}
        void RefreshInstalled(){launch.Enabled=!busy&&Installer.Installed;status.Text=Installer.Installed?"已经安装。可直接启动；遇到问题时再次点安装 / 修复。":"准备就绪。使用推荐设置，点“一键安装 / 修复”。";}
        void Busy(bool value){busy=value;install.Enabled=mode.Enabled=autoStart.Enabled=export.Enabled=!value;launch.Enabled=!value&&Installer.Installed;cancel.Enabled=value;RefreshMode();}
        void Append(string value){if(IsDisposed)return;if(InvokeRequired){try{BeginInvoke(new Action<string>(Append),value);}catch{}return;}string text=DateTime.Now.ToString("HH:mm:ss")+"  "+Files.Redact(value)+Environment.NewLine;if(logs.TextLength>160000)logs.Text=logs.Text.Substring(logs.TextLength-80000);logs.AppendText(text);try{Directory.CreateDirectory(Installer.Root);if(File.Exists(Installer.LogFile)&&new FileInfo(Installer.LogFile).Length>1500000){string old=Installer.LogFile+".previous";if(File.Exists(old))File.Delete(old);File.Move(Installer.LogFile,old);}File.AppendAllText(Installer.LogFile,text,new UTF8Encoding(false));}catch{}}
        async void InstallClick(object sender,EventArgs args){
            if(busy)return;string selected=mode.SelectedIndex==1?"turbo":mode.SelectedIndex==3?"srt":mode.SelectedIndex==2?"tiny":"small";bool wantGpu=gpu.Checked;bool wantMirror=mirror.Checked;Busy(true);cancellation=new CancellationTokenSource();var token=cancellation.Token;Append("开始安装 / 修复；模式："+selected+(wantGpu?"，尝试 NVIDIA 加速":"，CPU 模式")+(wantMirror?"，国内镜像已开启":""));
            var updates=new Progress<Update>(u=>{if(IsDisposed||!busy)return;status.Text=u.Text;progress.Style=u.Percent<0?ProgressBarStyle.Marquee:ProgressBarStyle.Continuous;if(u.Percent>=0)progress.Value=Math.Min(100,Math.Max(0,u.Percent));});bool ok=false;
            try{await Task.Run(()=>Installer.Install(selected,wantGpu,wantMirror,token,updates,Append));ok=true;Append("安装完成。以后可通过桌面快捷方式打开。");status.Text="安装完成。可以开始使用。";}
            catch(OperationCanceledException){status.Text="已取消；再次安装会复用已下载的文件。";Append(status.Text);}
            catch(Exception ex){status.Text="安装未完成。按下面日志提示处理，或导出安装诊断。";for(Exception e=ex;e!=null;e=e.InnerException)Append(e.GetType().Name+"："+e.Message);Append("先看“小白指南 / 常见问题”；不要重新下载整包，也不要手动删除已下载的大文件。");}
            finally{progress.Style=ProgressBarStyle.Continuous;cancellation.Dispose();cancellation=null;Busy(false);}
            if(closeAfter){Close();return;}
            if(ok&&autoStart.Checked)LaunchClick(this,EventArgs.Empty);
        }
        async void LaunchClick(object sender,EventArgs e){if(busy)return;launch.Enabled=false;try{await Task.Run(()=>Installer.Launch());status.Text="字幕工坊已打开，安装窗口可以关闭。";}catch(Exception ex){Append(ex.Message);Error(ex.Message);}finally{launch.Enabled=Installer.Installed;}}
        async void ExportClick(object sender,EventArgs e){
            string path;using(var dialog=new SaveFileDialog{Title="保存安装诊断（不包含 API 密钥、视频或字幕）",Filter="ZIP 文件 (*.zip)|*.zip",FileName="SubtitleStudio_安装诊断_"+DateTime.Now.ToString("yyyyMMdd_HHmmss")+".zip"}){if(dialog.ShowDialog(this)!=DialogResult.OK)return;path=dialog.FileName;}
            export.Enabled=false;try{await Task.Run(()=>{if(File.Exists(path))File.Delete(path);Installer.ExportDiagnostics(path);});MessageBox.Show(this,"已保存诊断 ZIP。把它发来，并说明最后点击了哪个按钮即可。","诊断已保存",MessageBoxButtons.OK,MessageBoxIcon.Information);}catch(Exception ex){Error(ex.Message);}finally{export.Enabled=true;}
        }
        void Error(string message){MessageBox.Show(this,Files.Redact(message),"字幕工坊",MessageBoxButtons.OK,MessageBoxIcon.Information);}
        void ClosingForm(object sender,FormClosingEventArgs e){if(!busy)return;e.Cancel=true;if(!closeAfter&&MessageBox.Show(this,"安装尚未结束。停止当前任务并退出？已下载文件保留。","退出安装",MessageBoxButtons.YesNo,MessageBoxIcon.Question)==DialogResult.Yes){closeAfter=true;cancel.Enabled=false;status.Text="正在结束当前操作，请稍等…";cancellation.Cancel();}}
    }
    internal static class Program {
        [STAThread] static void Main(string[] args){
            Application.EnableVisualStyles();Application.SetCompatibleTextRenderingDefault(false);bool first;
            using(var mutex=new Mutex(true,@"Local\SubtitleStudio.Easy.Setup",out first)){
                if(!first){MessageBox.Show("安装器已经打开，请切换到现有窗口。","字幕工坊");return;}
                if(!Environment.Is64BitOperatingSystem){MessageBox.Show("本包适用于 Windows 10/11 的 64 位 Intel / AMD 电脑。","系统不支持");return;}
                if(args.Contains("--launch")&&Installer.Installed){try{Installer.Launch();return;}catch(Exception ex){MessageBox.Show(Files.Redact(ex.Message),"字幕工坊");}}
                Application.Run(new SetupForm());
            }
        }
    }
}
