<!--
function getCookie(name)
{                                                                                                                     var cname = name + "=";                                                                                       var dc = document.cookie;                                                                                     if (dc.length > 0)
{
begin = dc.indexOf(cname);
if (begin != -1)                                                                                              {                                                                                                                     begin += cname.length;
end = dc.indexOf(";", begin);
if (end == -1) end = dc.length;
return unescape(dc.substring(begin, end));
}
}
return null;
}

function takeCookie()
{
var visitordata = getCookie('nas_lang');
if (visitordata == "TCH") mainkey = 'cht';
else
if (visitordata == "SCH") mainkey = 'chs';
else
if (visitordata == "JPN") mainkey = 'jpn';
else
if (visitordata == "KOR") mainkey = 'kor';
else
if (visitordata == "FRE") mainkey = 'fre';
else
if (visitordata == "GER") mainkey = 'ger';
else
if (visitordata == "ITA") mainkey = 'ita';
else
if (visitordata == "POR") mainkey = 'por';
else
if (visitordata == "SPA") mainkey = 'spa';
else
if (visitordata == "DUT") mainkey = 'dut';
else
if (visitordata == "NOR") mainkey = 'nor';
else
if (visitordata == "FIN") mainkey = 'fin';
else
if (visitordata == "SWE") mainkey = 'swe';
else
if (visitordata == "DAN") mainkey = 'dan';
else
if (visitordata == "RUS") mainkey = 'rus';
else
if (visitordata == "POL") mainkey = 'pol';
else mainkey = 'eng';
document.write("<script language=\"JavaScript\" src=\"/lang_" + mainkey + ".js\"><\/script>");
}


window.load =takeCookie();
