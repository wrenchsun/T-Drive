// メニューバーに「T-Drive」の最上位タブを作らない: パッケージの Editor アセンブリの MenuItem はすべて Tools/T-Drive/ の下にある。
using System;
using System.Collections.Generic;
using System.Linq;
using System.Reflection;
using NUnit.Framework;
using UnityEditor;

namespace TDrive.Facial.Tests
{
    public class FacialMenuPathTests
    {
        [Test]
        public void EveryMenuItemOfThePackageIsUnderToolsTDrive()
        {
            var found = new List<string>();
            foreach (Assembly a in AppDomain.CurrentDomain.GetAssemblies())
            {
                if (!a.GetName().Name.StartsWith("TDrive.Facial", StringComparison.Ordinal)) continue;
                Type[] types;
                try { types = a.GetTypes(); } catch (ReflectionTypeLoadException e) { types = e.Types.Where(t => t != null).ToArray(); }
                foreach (Type t in types)
                    foreach (MethodInfo m in t.GetMethods(BindingFlags.Static | BindingFlags.Public | BindingFlags.NonPublic))
                        foreach (MenuItem mi in m.GetCustomAttributes(typeof(MenuItem), false))
                            found.Add(mi.menuItem);
            }
            Assert.Greater(found.Count, 0, "MenuItem が 1 つも見つからない（検査が空振り）");
            foreach (string path in found)
                StringAssert.StartsWith("Tools/T-Drive/", path, "メニューは Tools の下に置く: " + path);
        }
    }
}
