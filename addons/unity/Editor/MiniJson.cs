using System;
using System.Collections.Generic;
using System.Globalization;
using System.Text;

namespace TwinScene.Editor
{
    /// <summary>
    /// Just enough JSON to read the twinscene command's output: objects become Dictionary&lt;string, object&gt;,
    /// arrays List&lt;object&gt;, numbers double. (Unity's JsonUtility can't read dictionaries or mixed arrays.)
    /// </summary>
    public static class MiniJson
    {
        public static object Parse(string text)
        {
            int i = 0;
            var v = Value(text, ref i);
            Skip(text, ref i);
            if (i != text.Length) throw new FormatException($"unexpected text at {i}");
            return v;
        }

        static void Skip(string s, ref int i)
        {
            while (i < s.Length && char.IsWhiteSpace(s[i])) i++;
        }

        static object Value(string s, ref int i)
        {
            Skip(s, ref i);
            if (i >= s.Length) throw new FormatException("unexpected end");
            char c = s[i];
            if (c == '{')
            {
                var d = new Dictionary<string, object>();
                i++;
                Skip(s, ref i);
                if (s[i] == '}') { i++; return d; }
                while (true)
                {
                    Skip(s, ref i);
                    var key = Str(s, ref i);
                    Skip(s, ref i);
                    if (s[i] != ':') throw new FormatException($"expected ':' at {i}");
                    i++;
                    d[key] = Value(s, ref i);
                    Skip(s, ref i);
                    if (s[i] == ',') { i++; continue; }
                    if (s[i] == '}') { i++; return d; }
                    throw new FormatException($"expected ',' or '}}' at {i}");
                }
            }
            if (c == '[')
            {
                var l = new List<object>();
                i++;
                Skip(s, ref i);
                if (s[i] == ']') { i++; return l; }
                while (true)
                {
                    l.Add(Value(s, ref i));
                    Skip(s, ref i);
                    if (s[i] == ',') { i++; continue; }
                    if (s[i] == ']') { i++; return l; }
                    throw new FormatException($"expected ',' or ']' at {i}");
                }
            }
            if (c == '"') return Str(s, ref i);
            if (s.Length - i >= 4 && string.CompareOrdinal(s, i, "true", 0, 4) == 0) { i += 4; return true; }
            if (s.Length - i >= 5 && string.CompareOrdinal(s, i, "false", 0, 5) == 0) { i += 5; return false; }
            if (s.Length - i >= 4 && string.CompareOrdinal(s, i, "null", 0, 4) == 0) { i += 4; return null; }
            int start = i;
            while (i < s.Length && "+-0123456789.eE".IndexOf(s[i]) >= 0) i++;
            if (start == i) throw new FormatException($"unexpected '{c}' at {i}");
            return double.Parse(s.Substring(start, i - start), CultureInfo.InvariantCulture);
        }

        static string Str(string s, ref int i)
        {
            if (s[i] != '"') throw new FormatException($"expected a string at {i}");
            i++;
            var sb = new StringBuilder();
            while (s[i] != '"')
            {
                char c = s[i++];
                if (c != '\\') { sb.Append(c); continue; }
                char e = s[i++];
                switch (e)
                {
                    case 'n': sb.Append('\n'); break;
                    case 't': sb.Append('\t'); break;
                    case 'r': sb.Append('\r'); break;
                    case 'b': sb.Append('\b'); break;
                    case 'f': sb.Append('\f'); break;
                    case 'u':
                        sb.Append((char)int.Parse(s.Substring(i, 4), NumberStyles.HexNumber));
                        i += 4;
                        break;
                    default: sb.Append(e); break;
                }
            }
            i++;
            return sb.ToString();
        }

        // -- small helpers for reading parsed values
        public static Dictionary<string, object> Obj(object o, string key) =>
            (o as Dictionary<string, object>) != null && ((Dictionary<string, object>)o).TryGetValue(key, out var v)
                ? v as Dictionary<string, object> : null;

        public static List<object> List(object o, string key) =>
            (o as Dictionary<string, object>) != null && ((Dictionary<string, object>)o).TryGetValue(key, out var v)
                ? v as List<object> ?? new List<object>() : new List<object>();

        public static string Text(object o, string key) =>
            (o as Dictionary<string, object>) != null && ((Dictionary<string, object>)o).TryGetValue(key, out var v) && v != null
                ? Convert.ToString(v, CultureInfo.InvariantCulture) : "";

        /// <summary>A value as short human text: [1.0, 2.5, 0] -> (1, 2.5, 0).</summary>
        public static string Show(object v)
        {
            if (v == null) return "(none)";
            if (v is List<object> l)
            {
                var parts = new List<string>();
                foreach (var x in l) parts.Add(Show(x));
                return "(" + string.Join(", ", parts) + ")";
            }
            if (v is double d) return d.ToString("0.###", CultureInfo.InvariantCulture);
            if (v is bool b) return b ? "on" : "off";
            return Convert.ToString(v, CultureInfo.InvariantCulture);
        }
    }
}
