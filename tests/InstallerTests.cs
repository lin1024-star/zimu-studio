using System;
using System.IO;
using System.IO.Compression;
using System.Linq;
using System.Collections.Generic;
using System.Security.Cryptography;
using System.Text;
using System.Threading;
using System.Runtime.InteropServices;
namespace SubtitleEasy {
 internal static class InstallerTests {
  static int passed; static string root; static readonly CancellationToken None=CancellationToken.None;
  sealed class Reporter:IProgress<Update>{public Action<Update> Act;public void Report(Update u){if(Act!=null)Act(u);}}
  static Reporter Quiet=new Reporter();
  static byte[] Bytes(string s){return Encoding.UTF8.GetBytes(s);}
  static Spec SpecFor(byte[] b){using(var sha=SHA256.Create())return new Spec{Name="fixture.bin",Url="https://example.invalid/fixture",Size=b.Length,Sha=BitConverter.ToString(sha.ComputeHash(b)).Replace("-","").ToLowerInvariant()};}
  static Reply Response(byte[] b,int status=200,string range=""){return new Reply{Body=new MemoryStream(b),Status=status,Range=range};}
  static void Check(bool ok,string why){if(!ok)throw new Exception(why);}
  static void Throws<T>(Action act) where T:Exception{try{act();}catch(T){return;}throw new Exception("Expected "+typeof(T).Name);}
  static void Test(string name,Action run){string prior=root;root=Path.Combine(prior,name);Directory.CreateDirectory(root);try{run();passed++;Console.WriteLine("PASS "+name);}finally{root=prior;}}
  static string Target{get{return Path.Combine(root,"result.bin");}}
  static string Zip(params string[] names){string path=Path.Combine(root,"test.zip");using(var z=ZipFile.Open(path,ZipArchiveMode.Create))foreach(string name in names)using(var w=new StreamWriter(z.CreateEntry(name).Open()))w.Write("fixture 你好");return path;}
  [DllImport("libc",SetLastError=true)]static extern int symlink(string target,string linkpath);
  public static int Main(string[] args){root=Path.Combine(Path.GetTempPath(),"subtitle-qa-"+Guid.NewGuid().ToString("N"));Directory.CreateDirectory(root);try{
   Test("download_publish_and_reuse",()=>{var data=Bytes("model-content");var spec=SpecFor(data);int calls=0;Func<string,long,CancellationToken,Reply> op=(u,o,t)=>{calls++;return Response(data);};Files.Fetch(spec,Target,None,Quiet,op);Files.Fetch(spec,Target,None,Quiet,op);Check(calls==1&&File.ReadAllBytes(Target).SequenceEqual(data),"Cache not reused");});
   Test("download_resume_exact",()=>{var data=Bytes("1234567890");File.WriteAllBytes(Target+".part",data.Take(4).ToArray());Files.Fetch(SpecFor(data),Target,None,Quiet,(u,o,t)=>{Check(o==4,"Missing Range offset");return Response(data.Skip(4).ToArray(),206,"bytes 4-9/10");});Check(File.ReadAllBytes(Target).SequenceEqual(data),"Wrong merged bytes");});
   Test("server_ignores_range_restarts",()=>{var data=Bytes("1234567890");File.WriteAllBytes(Target+".part",Bytes("1234"));Files.Fetch(SpecFor(data),Target,None,Quiet,(u,o,t)=>Response(data));Check(File.ReadAllBytes(Target).SequenceEqual(data),"Incorrect append after 200");});
   Test("truncated_download_resumes",()=>{var data=Bytes("1234567890");int count=0;Files.Fetch(SpecFor(data),Target,None,Quiet,(u,o,t)=>{count++;if(count==1)return Response(data.Take(4).ToArray());Check(o==4,"Retry discarded useful partial");return Response(data.Skip(4).ToArray(),206,"bytes 4-9/10");});Check(count==2&&File.ReadAllBytes(Target).SequenceEqual(data),"Truncated response not recovered");});
   Test("bad_range_never_appended",()=>{var data=Bytes("1234567890");File.WriteAllBytes(Target+".part",Bytes("1234"));Throws<IOException>(()=>Files.Fetch(SpecFor(data),Target,None,Quiet,(u,o,t)=>Response(Bytes("567890"),206,"bytes 5-10/10")));Check(!File.Exists(Target)&&File.ReadAllText(Target+".part")=="1234","Bad range corrupted partial");});
   Test("hash_mismatch_preserves_old_file",()=>{File.WriteAllText(Target,"old");var data=Bytes("1234567890");Throws<IOException>(()=>Files.Fetch(SpecFor(data),Target,None,Quiet,(u,o,t)=>Response(Bytes("abcdefghij"))));Check(File.ReadAllText(Target)=="old"&&!File.Exists(Target+".part"),"Wrong hash published");});
   Test("cancel_retains_partial",()=>{var data=Enumerable.Repeat((byte)7,600000).ToArray();using(var c=new CancellationTokenSource()){var report=new Reporter{Act=u=>{if(u.Percent>0)c.Cancel();}};Throws<OperationCanceledException>(()=>Files.Fetch(SpecFor(data),Target,c.Token,report,(u,o,t)=>Response(data)));Check(!File.Exists(Target)&&new FileInfo(Target+".part").Length>0&&new FileInfo(Target+".part").Length<data.Length,"Cancelled file published or discarded");}});
   Test("complete_partial_needs_no_network",()=>{var data=Bytes("whole");File.WriteAllBytes(Target+".part",data);Files.Fetch(SpecFor(data),Target,None,Quiet,(u,o,t)=>{throw new Exception("Unexpected HTTP");});Check(File.Exists(Target)&&!File.Exists(Target+".part"),"Complete partial not promoted");});
   Test("bad_spec_rejected_before_request",()=>{var spec=SpecFor(Bytes("abc"));spec.Url="http://example.invalid/fixture";Throws<IOException>(()=>Files.Fetch(spec,Target,None,Quiet,(u,o,t)=>{throw new Exception("Unsafe HTTP requested");}));});
   Test("zip_unicode_and_replace",()=>{var zip=Zip("目录/字幕.txt","readme.txt");var dir=Path.Combine(root,"out");Files.Extract(zip,dir,None);File.WriteAllText(Path.Combine(dir,"readme.txt"),"old");Files.Extract(zip,dir,None);Check(File.ReadAllText(Path.Combine(dir,"目录/字幕.txt")).Contains("你好")&&File.ReadAllText(Path.Combine(dir,"readme.txt")).Contains("你好"),"Extraction content wrong");});
   Test("zip_traversal_rejected",()=>{var zip=Zip("../outside.txt");Throws<IOException>(()=>Files.Extract(zip,Path.Combine(root,"out"),None));Check(!File.Exists(Path.Combine(root,"outside.txt")),"Wrote outside destination");});
   Test("zip_windows_path_rejected",()=>{var zip=Zip(@"nested\..\outside.txt");Throws<IOException>(()=>Files.Extract(zip,Path.Combine(root,"out"),None));});
   Test("zip_drive_path_rejected",()=>{var zip=Zip("C:/outside.txt");Throws<IOException>(()=>Files.Extract(zip,Path.Combine(root,"out"),None));});
   Test("zip_cancelled_before_write",()=>{var zip=Zip("readme.txt");var dir=Path.Combine(root,"out");using(var c=new CancellationTokenSource()){c.Cancel();Throws<OperationCanceledException>(()=>Files.Extract(zip,dir,c.Token));Check(!File.Exists(Path.Combine(dir,"readme.txt")),"Cancelled extraction wrote file");}});
   if(Environment.OSVersion.Platform==PlatformID.Unix){
    Test("zip_parent_link_rejected",()=>{string dir=Path.Combine(root,"out"),outside=Path.Combine(root,"outside");Directory.CreateDirectory(dir);Directory.CreateDirectory(outside);Check(symlink(outside,Path.Combine(dir,"linked"))==0,"Could not create fixture link");var zip=Zip("linked/child/data.txt");Throws<IOException>(()=>Files.Extract(zip,dir,None));Check(!Directory.Exists(Path.Combine(outside,"child")),"Created directory through link");});
    Test("zip_temporary_link_rejected",()=>{string dir=Path.Combine(root,"out"),outside=Path.Combine(root,"outside.txt");Directory.CreateDirectory(dir);File.WriteAllText(outside,"original");Check(symlink(outside,Path.Combine(dir,"readme.txt.unpacking"))==0,"Could not create link");var zip=Zip("readme.txt");Throws<IOException>(()=>Files.Extract(zip,dir,None));Check(File.ReadAllText(outside)=="original","Overwrote symlink target");});
   }
   Test("redact_key_url_home",()=>{string red=Files.Redact("sk-QA_PRIVATE_CANARY https://example.invalid/?token=secret "+Environment.GetFolderPath(Environment.SpecialFolder.UserProfile));Check(!red.Contains("QA_PRIVATE")&&!red.Contains("example.invalid")&&!red.Contains("token=secret"),"Secret in output");});
   Test("windows_argument_quoting",()=>{Check(Files.Quote("")=="\"\"","Empty argument");Check(Files.Quote("plain")=="\"plain\"","Plain argument");Check(Files.Quote("a\"b")=="\"a\\\"b\"","Quote escaping");Check(Files.Quote("C:\\with space\\")=="\"C:\\with space\\\\\"","Trailing slash escaping");});
   Test("manifest_and_embedded_payload",()=>{var m=Installer.Manifest();Check(Json.Get(m,"version")=="1.6.0","Wrong manifest version");var wheels=(List<object>)m["wheels"];Check(wheels.Count==25,"Unexpected wheel count");foreach(var w in wheels){var spec=Spec.Read((Dictionary<string,object>)w);Check(spec.Size>0&&spec.Sha.Length==64&&spec.Url.StartsWith("https://files.pythonhosted.org/"),"Unpinned wheel");}var models=(Dictionary<string,object>)m["models"];foreach(string name in new[]{"small","turbo"}){var model=(Dictionary<string,object>)models[name];var files=(List<object>)model["files"];Check(files.Cast<Dictionary<string,object>>().Any(d=>Json.Get(d,"name")=="model.bin"),"Missing model");}using(var s=System.Reflection.Assembly.GetExecutingAssembly().GetManifestResourceStream("payload.zip"))using(var z=new ZipArchive(s,ZipArchiveMode.Read)){Check(z.GetEntry("start_app.py")!=null&&z.GetEntry("guide.html")!=null&&z.GetEntry("requirements-windows.lock")!=null,"Embedded payload incomplete");}});
   if(args.Length>0)Test("official_python_zip_extract",()=>{string outdir=Path.Combine(root,"python");Files.Extract(args[0],outdir,None);foreach(string name in new[]{"python.exe","pythonw.exe","python313.dll","DLLs/_tkinter.pyd","Lib/tkinter/__init__.py","Lib/site-packages/pip/__main__.py"})Check(File.Exists(Path.Combine(outdir,name)),"Missing runtime file: "+name);});
   Console.WriteLine("PASS TOTAL "+passed);return 0;
  }catch(Exception e){Console.Error.WriteLine("FAILED: "+e);return 1;}finally{try{Directory.Delete(root,true);}catch{}}
  }
 }
}
