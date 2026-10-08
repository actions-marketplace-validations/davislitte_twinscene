using System;
using System.Collections.Concurrent;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Text;
using System.Threading.Tasks;
using UnityEditor;
using UnityEngine;

namespace TwinScene.Editor
{
    /// <summary>
    /// Runs the twinscene command line, which does all the real work; the add-on is a window onto it.
    /// The command is found automatically (twinscene, py -3 -m twinscene, python -m twinscene, ...), or set it in the
    /// TwinScene window, or with the TWINSCENE_COMMAND environment variable.
    /// </summary>
    public static class TwinSceneCli
    {
        public const string CommandPref = "TwinScene.Command";
        static readonly string[] Candidates =
            { "twinscene", "py -3 -m twinscene", "python -m twinscene", "python3 -m twinscene" };
        static readonly ConcurrentQueue<Action> MainThread = new ConcurrentQueue<Action>();
        static string _root, _detected;
        static bool _rootSearched;

        public struct Result
        {
            public int ExitCode;
            public string Stdout, Stderr;
            public object Json;
            /// <summary>Set when the command couldn't run or reported an error.</summary>
            public string Error;
            public bool Ok => Error == null;
        }

        static TwinSceneCli()
        {
            EditorApplication.update += () =>
            {
                while (MainThread.TryDequeue(out var a)) a();
            };
        }

        /// <summary>The folder holding twinscene.json, above this Unity project (null if there isn't one).</summary>
        public static string RepoRoot
        {
            get
            {
                if (_rootSearched) return _root;
                _rootSearched = true;
                for (var d = new DirectoryInfo(Path.GetDirectoryName(Application.dataPath)); d != null; d = d.Parent)
                    if (File.Exists(Path.Combine(d.FullName, "twinscene.json"))) return _root = d.FullName;
                return null;
            }
        }

        public static void ForgetRoot()
        {
            _rootSearched = false;
            _root = null;
        }

        /// <summary>The command in use: the env override, the saved setting, or the first one found to work.</summary>
        public static string Command
        {
            get
            {
                var env = Environment.GetEnvironmentVariable("TWINSCENE_COMMAND");
                if (!string.IsNullOrWhiteSpace(env)) return env.Trim();
                var pref = EditorPrefs.GetString(CommandPref, "");
                if (!string.IsNullOrWhiteSpace(pref)) return pref.Trim();
                if (_detected != null) return _detected;
                foreach (var c in Candidates)
                    if (Exec(c, new[] { "--version" }, null, 15000).ExitCode == 0) return _detected = c;
                return _detected = "";
            }
        }

        public static void SetCommand(string command)
        {
            EditorPrefs.SetString(CommandPref, command ?? "");
            _detected = null;
        }

        /// <summary>Run twinscene with these arguments (plus --root) and wait. Output with --json is parsed.</summary>
        public static Result Run(params string[] args)
        {
            var root = RepoRoot;
            if (root == null) return new Result { Error = "This project isn't in a TwinScene repository (no twinscene.json found above it)." };
            var cmd = Command;
            if (cmd == "") return new Result { Error = "Can't find the twinscene command. Install it with: pip install twinscene\n(or set the command in Window > TwinScene)." };
            var r = Exec(cmd, new[] { "--root", root }.Concat(args).ToArray(), root, 300000);
            if (r.Error != null) return r;
            if (args.Contains("--json"))
            {
                try
                {
                    var last = r.Stdout.Split('\n').LastOrDefault(l => l.TrimStart().StartsWith("{"));
                    r.Json = last == null ? null : MiniJson.Parse(last.Trim());
                }
                catch (Exception e)
                {
                    r.Error = "Couldn't read twinscene's output: " + e.Message;
                    return r;
                }
                var err = MiniJson.Text(r.Json, "error");
                if (err != "") r.Error = err;
            }
            if (r.Error == null && r.ExitCode != 0 && r.ExitCode != 3)  // 3 = finished, with conflicts to settle
                r.Error = (r.Stderr + "\n" + r.Stdout).Trim();
            return r;
        }

        /// <summary>Run in the background; done is called on the main thread.</summary>
        public static void RunAsync(Action<Result> done, params string[] args)
        {
            if (Application.isBatchMode)  // no editor loop to call us back: just run it
            {
                done(Run(args));
                return;
            }
            Task.Run(() =>
            {
                Result r;
                try { r = Run(args); }
                catch (Exception e) { r = new Result { Error = e.Message }; }
                MainThread.Enqueue(() => done(r));
            });
        }

        static Result Exec(string command, string[] args, string cwd, int timeoutMs)
        {
            var parts = Split(command);
            var psi = new ProcessStartInfo
            {
                FileName = parts[0],
                Arguments = string.Join(" ", parts.Skip(1).Concat(args).Select(Quote)),
                UseShellExecute = false,
                CreateNoWindow = true,
                RedirectStandardOutput = true,
                RedirectStandardError = true,
                StandardOutputEncoding = Encoding.UTF8,
                StandardErrorEncoding = Encoding.UTF8,
            };
            if (cwd != null) psi.WorkingDirectory = cwd;
            psi.EnvironmentVariables["PYTHONIOENCODING"] = "utf-8";
            try
            {
                using (var p = Process.Start(psi))
                {
                    var err = p.StandardError.ReadToEndAsync();
                    var output = p.StandardOutput.ReadToEnd();
                    if (!p.WaitForExit(timeoutMs))
                    {
                        try { p.Kill(); } catch { /* already gone */ }
                        return new Result { ExitCode = -1, Error = $"twinscene took longer than {timeoutMs / 1000} s and was stopped." };
                    }
                    return new Result { ExitCode = p.ExitCode, Stdout = output, Stderr = err.Result };
                }
            }
            catch (Exception e)
            {
                return new Result { ExitCode = -1, Error = $"Couldn't start \"{command}\": {e.Message}" };
            }
        }

        static List<string> Split(string command)
        {
            var parts = new List<string>();
            var cur = new StringBuilder();
            bool quoted = false;
            foreach (var c in command)
            {
                if (c == '"') { quoted = !quoted; continue; }
                if (char.IsWhiteSpace(c) && !quoted)
                {
                    if (cur.Length > 0) { parts.Add(cur.ToString()); cur.Clear(); }
                    continue;
                }
                cur.Append(c);
            }
            if (cur.Length > 0) parts.Add(cur.ToString());
            return parts;
        }

        static string Quote(string a) =>
            a.Length > 0 && a.IndexOfAny(new[] { ' ', '\t', '"' }) < 0 ? a : "\"" + a.Replace("\\\"", "\\\\\"").Replace("\"", "\\\"") + "\"";
    }
}
