-- 适用于 Hyprland 0.56.2 Lua 配置；由用户显式 dofile 加载。
-- 不设置 opaque/force_rgbx；保留应用自己的透明通道。
hl.window_rule({
  name = "wecom-fixes-main",
  match = { class = "(?i)^(wxwork|wxworkweb|wwmapp)\\.exe$" },
  float = true,
  opacity = "1.0 override 1.0 override 1.0 override",
  no_anim = true,
  no_blur = true,
  no_shadow = true,
  border_size = 0,
  rounding = 0,
})

-- 只抑制空标题装饰层的焦点，不把真实菜单放进这个规则。
hl.window_rule({
  name = "wecom-fixes-decoration",
  match = { class = "(?i)^wxwork\\.exe$", title = "^$" },
  float = true,
  no_focus = true,
  no_initial_focus = true,
  no_anim = true,
  no_blur = true,
  no_shadow = true,
})

hl.window_rule({
  name = "wecom-fixes-menu",
  match = { class = "(?i)^wxwork\\.exe$", title = "^menu$" },
  no_focus = false,
  no_initial_focus = true,
  no_follow_mouse = true,
  tag = "+wecom-fixes-menu",
})

-- 搜索结果窗由主窗处理键盘输入；显示或悬停时不能抢走焦点。
-- 保留鼠标输入，搜索结果仍可点击；空标题阴影继续使用装饰层规则。
hl.window_rule({
  name = "wecom-fixes-search",
  match = {
    class = "(?i)^wxwork\\.exe$",
    title = "^SearchResultWindow2$",
  },
  no_focus = false,
  no_initial_focus = true,
  no_follow_mouse = true,
})

-- 通过动态标签限定到企业微信菜单；不匹配其他应用的同名菜单。
hl.bind("mouse:272", hl.dsp.pass({ window = "tag:wecom-fixes-menu" }), {
  auto_consuming = true,
  description = "企业微信菜单点击转交",
})

hl.window_rule({
  name = "wecom-fixes-file-dialog",
  match = {
    class = "(?i)^wxwork\\.exe$",
    title = "^(Select|Open|Save|选择|打开|保存).*",
  },
  float = true,
  dim_around = true,
})
