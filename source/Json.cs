using System;
using System.Collections;
using System.Collections.Generic;
using System.Globalization;
using System.Text;

namespace SubtitleEasy {
    // Small data-only JSON reader/writer. No script engine, reflection or type activation.
    internal static class Json {
        internal static object Parse(string text) { var p=new Reader(text); var v=p.Value(0); p.Space(); if(p.Position!=text.Length)throw new FormatException("JSON 尾部无效");return v; }
        internal static Dictionary<string,object> Object(string text) { var d=Parse(text) as Dictionary<string,object>;if(d==null)throw new FormatException("JSON 对象无效");return d; }
        internal static string Get(Dictionary<string,object> d,string key,string fallback="") { object v;return d!=null&&d.TryGetValue(key,out v)&&v!=null?Convert.ToString(v,CultureInfo.InvariantCulture):fallback; }
        internal static string Write(object value) {
            if(value==null)return "null";
            if(value is string) {var b=new StringBuilder("\"");foreach(char c in (string)value){switch(c){case '"':b.Append("\\\"");break;case '\\':b.Append("\\\\");break;case '\n':b.Append("\\n");break;case '\r':b.Append("\\r");break;case '\t':b.Append("\\t");break;default:if(c<32)b.Append("\\u"+((int)c).ToString("x4"));else b.Append(c);break;}}return b.Append('"').ToString();}
            if(value is bool)return (bool)value?"true":"false";
            var dict=value as IDictionary;if(dict!=null){var rows=new List<string>();foreach(DictionaryEntry x in dict)rows.Add(Write(Convert.ToString(x.Key))+":"+Write(x.Value));return "{"+String.Join(",",rows)+"}";}
            var list=value as IEnumerable;if(list!=null){var rows=new List<string>();foreach(object x in list)rows.Add(Write(x));return "["+String.Join(",",rows)+"]";}
            return Convert.ToString(value,CultureInfo.InvariantCulture);
        }
        sealed class Reader {
            readonly string s;public int Position;
            public Reader(string text){if(text==null||text.Length>16000000)throw new FormatException("JSON 过大");s=text;}
            public void Space(){while(Position<s.Length&&Char.IsWhiteSpace(s[Position]))Position++;}
            char Take(){if(Position>=s.Length)throw new FormatException("JSON 不完整");return s[Position++];}
            public object Value(int depth){
                if(depth>64)throw new FormatException("JSON 嵌套过深");Space();if(Position>=s.Length)throw new FormatException("JSON 为空");char c=s[Position];
                if(c=='"')return Text();
                if(c=='{'){Position++;var d=new Dictionary<string,object>();Space();if(Position<s.Length&&s[Position]=='}'){Position++;return d;}while(true){Space();string key=Text();Space();if(Take()!=':')throw new FormatException();d[key]=Value(depth+1);Space();char sep=Take();if(sep=='}')return d;if(sep!=',')throw new FormatException();}}
                if(c=='['){Position++;var a=new List<object>();Space();if(Position<s.Length&&s[Position]==']'){Position++;return a;}while(true){a.Add(Value(depth+1));Space();char sep=Take();if(sep==']')return a;if(sep!=',')throw new FormatException();}}
                foreach(string word in new[]{"true","false","null"})if(s.Length-Position>=word.Length&&s.Substring(Position,word.Length)==word){Position+=word.Length;return word=="null"?null:(object)(word=="true");}
                int start=Position;while(Position<s.Length&&"-+0123456789.eE".IndexOf(s[Position])>=0)Position++;
                double n;if(Position==start||!Double.TryParse(s.Substring(start,Position-start),NumberStyles.Float,CultureInfo.InvariantCulture,out n)||Double.IsNaN(n)||Double.IsInfinity(n))throw new FormatException("JSON 数字无效");return n;
            }
            string Text(){if(Take()!='"')throw new FormatException("JSON 字符串无效");var b=new StringBuilder();while(true){char c=Take();if(c=='"')return b.ToString();if(c<32)throw new FormatException();if(c!='\\'){b.Append(c);continue;}c=Take();switch(c){case '"':case '\\':case '/':b.Append(c);break;case 'b':b.Append('\b');break;case 'f':b.Append('\f');break;case 'n':b.Append('\n');break;case 'r':b.Append('\r');break;case 't':b.Append('\t');break;case 'u':if(Position+4>s.Length)throw new FormatException();b.Append((char)Int32.Parse(s.Substring(Position,4),NumberStyles.HexNumber));Position+=4;break;default:throw new FormatException();}}}
        }
    }
}
