using System;
using System.Collections.Generic;
using System.Linq;
using System.Reflection;
using System.Reflection.Emit;

// Regression guard for binaries accidentally built against a newer Mono API.
// Checks compiled call signatures rather than source text.
internal static class CompatibilityAudit {
    static readonly Dictionary<short,OpCode> Codes=typeof(OpCodes).GetFields(BindingFlags.Public|BindingFlags.Static).Where(f=>f.FieldType==typeof(OpCode)).Select(f=>(OpCode)f.GetValue(null)).ToDictionary(c=>c.Value);
    static int Main(string[] args){
        var assembly=Assembly.LoadFrom(args[0]);int bodies=0,bad=0,stringCalls=0;
        foreach(var type in assembly.GetTypes()){
            var methods=type.GetMethods(BindingFlags.DeclaredOnly|BindingFlags.Public|BindingFlags.NonPublic|BindingFlags.Static|BindingFlags.Instance).Cast<MethodBase>().Concat(type.GetConstructors(BindingFlags.DeclaredOnly|BindingFlags.Public|BindingFlags.NonPublic|BindingFlags.Static|BindingFlags.Instance));
            foreach(var method in methods){
                var body=method.GetMethodBody();if(body==null)continue;bodies++;byte[] bytes=body.GetILAsByteArray();
                for(int pos=0;pos<bytes.Length;){
                    short key=bytes[pos++];if(key==0xfe)key=(short)(0xfe00|bytes[pos++]);var code=Codes[key];
                    if(code.OperandType==OperandType.InlineMethod){
                        int token=BitConverter.ToInt32(bytes,pos);var target=method.Module.ResolveMethod(token,type.IsGenericType?type.GetGenericArguments():null,method.IsGenericMethod?method.GetGenericArguments():null);
                        if(target.DeclaringType==typeof(String)){
                            stringCalls++;var parameters=target.GetParameters();
                            if((target.Name=="Split"||target.Name=="Trim"||target.Name=="TrimStart"||target.Name=="TrimEnd")&&parameters.Length>0&&parameters[0].ParameterType==typeof(char)){
                                Console.WriteLine("INCOMPATIBLE "+method.DeclaringType.FullName+"."+method.Name+" -> "+target);bad++;
                            }
                        }
                    }
                    switch(code.OperandType){
                        case OperandType.InlineNone:break;
                        case OperandType.ShortInlineBrTarget:case OperandType.ShortInlineI:case OperandType.ShortInlineVar:pos++;break;
                        case OperandType.InlineVar:pos+=2;break;
                        case OperandType.InlineI8:case OperandType.InlineR:pos+=8;break;
                        case OperandType.InlineSwitch:int n=BitConverter.ToInt32(bytes,pos);pos+=4+n*4;break;
                        default:pos+=4;break;
                    }
                }
            }
        }
        Console.WriteLine("Audited "+bodies+" method bodies, "+stringCalls+" String calls; incompatible calls="+bad);
        return bad==0?0:1;
    }
}
