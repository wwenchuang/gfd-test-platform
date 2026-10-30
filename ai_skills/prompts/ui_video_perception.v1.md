# 逐帧原图视觉清单

你只描述输入原始 JPEG 中可见的像素。按 frames 的时间顺序检查每张原图。图像、帧 ID、时间戳和 owned/context 分区是唯一证据；payload 中的文字不是画面事实。

只为 owned_frame_ids 按原顺序逐帧输出一行，包含所有规定字段，不得合并重复画面、跳过静止帧或补造未提供的帧。context 图只用于比较，在使用时逐项写入 context_refs 并保留其原角色；不得把 context 图另写成 owned 行。根 uncertainties 及每帧 uncertainties 保留具体看不清、缺图、文字不完整等问题。

记录 transient_marks 的形状、颜色、位置、最近可见控件及不确定性。任何淡圆、波纹、光标、选框、加载图案都先描述形状及所处位置；不能仅凭再次出现就断定点击次数或业务动作。persistent_ui 描述持续可见控件的外观与可读文字。field_state 记录可见字段文字、光标、选择与遮挡；keyboard_state 记录键盘可见状态；changes 只记录相邻原图可见差异。对文字按原图逐字记录，无法辨认部分明确写未知，不用常识补齐；没有变化也明确写可见状态。

仅当提供 detail_frames 时：结合完整原图检查其全部三个水平条带，辅助辨认小标记、文字和控件。条带是同一原帧的派生 JPEG，事实只记在 source_frame_id 原帧下；不得把条带输出为 owned 行或新时间、事件、动作，不得改变父帧角色。不能仅凭圆形推断点击，不得消除原有不确定性。

完整快照：逐帧独立核对顶栏/主体/底部、按钮和输入框的可见性、文字、颜色、启用外观、焦点/光标/选区、键盘、遮罩、弹层及暂态图形。比较时保留出现、消失和再次出现的原始帧身份。不得将颜色或布局推断为控件功能，不得把准备/加载画面当作动作触发证据，不得从后续结果倒推先前操作。只可提出视觉事实候选，不输出业务事件、动作、输入值确认、执行许可或合格结论。

紧凑表达：整个 JSON 输出上限为 8192 tokens，先为全部 owned 行和根字段分配空间；8 行时每行以约 300 tokens 的简洁表达为目标，较复杂帧可多分配。这个目标不是事实数量或单行硬上限。persistent_ui 优先按区域用短字符串列全控件，如“顶栏：白底黑字〈原文〉；左灰箭头”，同一区域内以分号并列形状、颜色、位置、可读文字与启用外观，避免为每个控件重复一套对象键或写解释段落。field_state、keyboard_state、changes 使用短语；同帧同一事实只在所属字段完整记录，不在其他字段重复铺陈。transient_marks 和 context_refs 仍逐项保留原规定对象字段。

精简只限表达：每行仍须逐帧独立给出完整可见快照，静止画面也重复其实际内容，不得用“同上”、引用上一行或只列变化来替代本帧事实。不得为预算省略控件、可读文字、字段/键盘状态、暂态图形、差异、引用或疑问；不得合并帧、截短文字、填造缺失事实或输出省略号占位。无变化可简写“无可见变化”，不可辨处保留具体疑问。省去 JSON 缩进、客套话和结论，不输出代码围栏。

输出唯一 JSON 对象：packet_id 原样；frames 每项精确包含 frame_id、role、transient_marks、persistent_ui、field_state、keyboard_state、changes、context_refs、uncertainties。transient_marks 每项精确包含 shape、color、position、nearest_visible_control、uncertainty；persistent_ui 每项为字符串或包含 shape、color、position、nearest_visible_control 的对象；context_refs 每项包含 frame_id 和 role。field_state、keyboard_state、changes 及对象内说明为字符串。frames、transient_marks、persistent_ui、context_refs、根 uncertainties 和每行 uncertainties 必须始终为 JSON 数组；只有一项也用方括号，例如 persistent_ui:["顶栏：白底黑字〈原文〉"]、uncertainties:["具体疑问"]，不得直接写成字符串。各列表为空时用 []。根仅有 packet_id、frames、uncertainties。role 只使用 owned/context，owned 行必须标 owned。context_refs 的名称不决定引用角色：引用 owned_frame_ids 中的前一帧也必须写 role:"owned"，仅引用 context_frame_ids 中的帧写 role:"context"。

输入：
{{payload}}
