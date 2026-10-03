// 依存なしの小さな JSON 読み取り（テスト専用。Unity の型・System.Text.Json を使わない）。
// 結果: オブジェクト = Dictionary<string, object>、配列 = List<object>、数 = double、文字列 = string、真偽 = bool、null = null。
using System;
using System.Collections.Generic;
using System.Globalization;
using System.Text;

namespace TDrive.Facial.Tests
{
    public static class MiniJson
    {
        public static object Parse(string text)
        {
            if (text.Length > 0 && text[0] == '﻿') text = text.Substring(1); // BOM
            var p = new Parser(text);
            p.SkipWs();
            object v = p.ReadValue();
            p.SkipWs();
            if (!p.AtEnd) throw p.Error("末尾に余計な文字");
            return v;
        }

        sealed class Parser
        {
            readonly string _s;
            int _i;

            public Parser(string s) { _s = s; }

            public bool AtEnd { get { return _i >= _s.Length; } }

            public Exception Error(string msg)
            {
                return new FormatException("JSON: " + msg + "（位置 " + _i + "）");
            }

            public void SkipWs()
            {
                while (_i < _s.Length && (_s[_i] == ' ' || _s[_i] == '\t' || _s[_i] == '\n' || _s[_i] == '\r')) _i++;
            }

            public object ReadValue()
            {
                if (AtEnd) throw Error("値が無い");
                char c = _s[_i];
                if (c == '{') return ReadObject();
                if (c == '[') return ReadArray();
                if (c == '"') return ReadString();
                if (c == 't') { Expect("true"); return true; }
                if (c == 'f') { Expect("false"); return false; }
                if (c == 'n') { Expect("null"); return null; }
                return ReadNumber();
            }

            void Expect(string word)
            {
                if (string.CompareOrdinal(_s, _i, word, 0, word.Length) != 0) throw Error("'" + word + "' が必要");
                _i += word.Length;
            }

            Dictionary<string, object> ReadObject()
            {
                var d = new Dictionary<string, object>();
                _i++; // {
                SkipWs();
                if (_i < _s.Length && _s[_i] == '}') { _i++; return d; }
                while (true)
                {
                    SkipWs();
                    if (AtEnd || _s[_i] != '"') throw Error("キーが必要");
                    string key = ReadString();
                    SkipWs();
                    if (AtEnd || _s[_i] != ':') throw Error("':' が必要");
                    _i++;
                    SkipWs();
                    d[key] = ReadValue();
                    SkipWs();
                    if (AtEnd) throw Error("オブジェクトが閉じていない");
                    if (_s[_i] == ',') { _i++; continue; }
                    if (_s[_i] == '}') { _i++; return d; }
                    throw Error("',' か '}' が必要");
                }
            }

            List<object> ReadArray()
            {
                var l = new List<object>();
                _i++; // [
                SkipWs();
                if (_i < _s.Length && _s[_i] == ']') { _i++; return l; }
                while (true)
                {
                    SkipWs();
                    l.Add(ReadValue());
                    SkipWs();
                    if (AtEnd) throw Error("配列が閉じていない");
                    if (_s[_i] == ',') { _i++; continue; }
                    if (_s[_i] == ']') { _i++; return l; }
                    throw Error("',' か ']' が必要");
                }
            }

            string ReadString()
            {
                _i++; // "
                var sb = new StringBuilder();
                while (true)
                {
                    if (AtEnd) throw Error("文字列が閉じていない");
                    char c = _s[_i++];
                    if (c == '"') return sb.ToString();
                    if (c != '\\') { sb.Append(c); continue; }
                    if (AtEnd) throw Error("エスケープが途中");
                    char e = _s[_i++];
                    switch (e)
                    {
                        case '"': sb.Append('"'); break;
                        case '\\': sb.Append('\\'); break;
                        case '/': sb.Append('/'); break;
                        case 'b': sb.Append('\b'); break;
                        case 'f': sb.Append('\f'); break;
                        case 'n': sb.Append('\n'); break;
                        case 'r': sb.Append('\r'); break;
                        case 't': sb.Append('\t'); break;
                        case 'u':
                            if (_i + 4 > _s.Length) throw Error("\\u が途中");
                            sb.Append((char)int.Parse(_s.Substring(_i, 4), NumberStyles.HexNumber, CultureInfo.InvariantCulture));
                            _i += 4;
                            break;
                        default: throw Error("未知のエスケープ \\" + e);
                    }
                }
            }

            double ReadNumber()
            {
                int start = _i;
                while (_i < _s.Length)
                {
                    char c = _s[_i];
                    if ((c >= '0' && c <= '9') || c == '-' || c == '+' || c == '.' || c == 'e' || c == 'E') _i++;
                    else break;
                }
                if (start == _i) throw Error("数が必要");
                double v;
                if (!double.TryParse(_s.Substring(start, _i - start), NumberStyles.Float, CultureInfo.InvariantCulture, out v))
                    throw Error("数が読めない");
                return v;
            }
        }
    }
}
