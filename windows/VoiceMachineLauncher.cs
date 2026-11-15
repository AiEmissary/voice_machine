using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Runtime.InteropServices;
using System.Text;
using System.Threading;
using System.Web.Script.Serialization;

// A pinnable GUI launcher for the existing non-activating Speak V2 overlay.
// Bringing it back does not click S, record audio, or replace the current text target.
internal static class VoiceMachineLauncher {
  delegate bool EnumWindow(IntPtr window,IntPtr parameter);
  [StructLayout(LayoutKind.Sequential)] struct Rect {public int Left,Top,Right,Bottom;}
  [StructLayout(LayoutKind.Sequential)] struct MonitorInfo {public int Size;public Rect Monitor,Work;public uint Flags;}
  [DllImport("user32.dll")] static extern bool EnumWindows(EnumWindow callback,IntPtr parameter);
  [DllImport("user32.dll",CharSet=CharSet.Unicode)] static extern int GetWindowText(IntPtr window,StringBuilder text,int length);
  [DllImport("user32.dll")] static extern uint GetWindowThreadProcessId(IntPtr window,out uint process);
  [DllImport("user32.dll")] static extern bool IsWindowVisible(IntPtr window);
  [DllImport("user32.dll")] static extern bool IsIconic(IntPtr window);
  [DllImport("user32.dll")] static extern bool GetWindowRect(IntPtr window,out Rect rectangle);
  [DllImport("user32.dll")] static extern IntPtr MonitorFromWindow(IntPtr window,uint flags);
  [DllImport("user32.dll",CharSet=CharSet.Unicode)] static extern bool GetMonitorInfo(IntPtr monitor,ref MonitorInfo info);
  [DllImport("user32.dll")] static extern bool ShowWindowAsync(IntPtr window,int command);
  [DllImport("user32.dll")] static extern bool SetWindowPos(IntPtr window,IntPtr after,int x,int y,int width,int height,uint flags);
  [DllImport("user32.dll",CharSet=CharSet.Unicode)] static extern int MessageBox(IntPtr owner,string text,string title,uint flags);
  [DllImport("shell32.dll",CharSet=CharSet.Unicode)] static extern int SetCurrentProcessExplicitAppUserModelID(string id);

  static List<IntPtr> FindButtons(){
    var found=new List<IntPtr>();
    EnumWindows(delegate(IntPtr window,IntPtr unused){
      var title=new StringBuilder(256);GetWindowText(window,title,title.Capacity);
      if(title.ToString()!="Speak V2")return true;
      uint id;GetWindowThreadProcessId(window,out id);
      try{using(var process=Process.GetProcessById((int)id)){
        if(process.ProcessName.Equals("pythonw",StringComparison.OrdinalIgnoreCase)||process.ProcessName.Equals("python",StringComparison.OrdinalIgnoreCase))found.Add(window);
      }}catch{}
      return true;
    },IntPtr.Zero);
    found.Sort(delegate(IntPtr a,IntPtr b){return IsWindowVisible(b).CompareTo(IsWindowVisible(a));});
    return found;
  }
  static void ShowButton(IntPtr window){
    if(IsIconic(window))ShowWindowAsync(window,9);else ShowWindowAsync(window,4);
    Rect rectangle;GetWindowRect(window,out rectangle);
    var info=new MonitorInfo();info.Size=Marshal.SizeOf(typeof(MonitorInfo));
    uint flags=0x0010|0x0040|0x0001; // NOACTIVATE | SHOWWINDOW | NOSIZE
    int x=rectangle.Left,y=rectangle.Top;
    if(GetMonitorInfo(MonitorFromWindow(window,2),ref info)){
      int width=Math.Max(40,rectangle.Right-rectangle.Left),height=Math.Max(40,rectangle.Bottom-rectangle.Top);
      x=Math.Max(info.Work.Left,Math.Min(x,info.Work.Right-width));
      y=Math.Max(info.Work.Top,Math.Min(y,info.Work.Bottom-height));
    }
    SetWindowPos(window,new IntPtr(-1),x,y,0,0,flags); // Preserve the floating, non-activating button.
  }
  [STAThread] static int Main(string[] args){
    try{
      SetCurrentProcessExplicitAppUserModelID("AiEmissary.VoiceMachine");
      var serializer=new JavaScriptSerializer();
      string folder=AppDomain.CurrentDomain.BaseDirectory;
      var config=serializer.Deserialize<Dictionary<string,string>>(File.ReadAllText(Path.Combine(folder,"launcher.json")));
      string python=Path.GetFullPath(config["python"]),script=Path.GetFullPath(config["script"]);
      if(!File.Exists(python)||!File.Exists(script))throw new FileNotFoundException("The configured voice app or Python runtime is missing.");
      if(!Path.GetFileName(python).Equals("pythonw.exe",StringComparison.OrdinalIgnoreCase)||Path.GetFileName(script)!="run.py")throw new InvalidDataException("Unexpected voice launcher configuration.");
      var buttons=FindButtons();
      if(args.Length==1&&args[0]=="--check"){
        Console.WriteLine(serializer.Serialize(new {success=true,matchingButtons=buttons.Count,visibleButtons=buttons.FindAll(IsWindowVisible).Count,configuredFilesExist=true}));return 0;
      }
      if(args.Length!=0)throw new ArgumentException("Unsupported launcher arguments.");
      bool owned;using(var mutex=new Mutex(true,"Local\\AiEmissary.VoiceMachine.TaskbarLauncher",out owned)){
        if(!owned)return 0;
        try{
          if(buttons.Count>0){ShowButton(buttons[0]);return 0;}
          var start=new ProcessStartInfo(python,"\""+script+"\""){WorkingDirectory=Path.GetDirectoryName(script),UseShellExecute=false,CreateNoWindow=true,WindowStyle=ProcessWindowStyle.Hidden};
          using(var process=Process.Start(start)){
            for(int i=0;i<300;i++){
              Thread.Sleep(100);buttons=FindButtons();
              if(buttons.Count>0){ShowButton(buttons[0]);return 0;}
              if(process!=null&&process.HasExited)break;
            }
          }
          throw new InvalidOperationException("The S button has not appeared yet. If the voice app is warming up, try the shortcut again shortly.");
        }finally{mutex.ReleaseMutex();}
      }
    }catch(Exception error){
      if(args.Length>0){Console.Error.WriteLine(error.Message);return 1;}
      MessageBox(IntPtr.Zero,error.Message,"Voice Machine",0x10);return 1;
    }
  }
}
