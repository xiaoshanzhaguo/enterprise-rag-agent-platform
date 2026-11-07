# R4 证据金标 AI 辅助审核（2026-11-06）

第一轮审核者：Codex。38题、37条原始证据逐项对照12份原文；不是人工确认或盲审，原候选集也由Codex生成。本轮没有依据检索命中/Recall结果确认相关性，没有调用项目模型API。

## 结论与边界

- 题目：PASS 36，REVISE 2，REJECT 0，UNCERTAIN 0。
- 原始证据：36条建议保留，1条建议移出正相关集合（it_03:e2）；原文/偏移校验37/37通过。另有3个条件性或可选跨度已程序定位，不写入金标。
- 36个PASS含5个流程题的检索N/A设置、7个冻结语料内无正证据判断；它们不代表正证据召回、字段提取或生成拒答已通过。
- 38题及37条证据仍保持pending_review，未改confirmed；未改评分体系、历史报告、生产代码或数据库，未提交Commit。

**实际清单是 corpus.json，目录没有 corpus_manifest.json。** 数据没有业务document_id，以实际document_key（来源+V1+内容摘要）核验。79个冻结Chunk目录中的临时Document ID仅供一致性追踪，不作为业务ID；未连接业务SQLite/Chroma，不能声称核验了业务当前版本指针。以下PASS仅适用于冻结语料V1。

偏移采用UTF-8读取、统一LF后的Python字符索引，0起始、end不包含；不是字节、UTF-16或Markdown渲染位置。

## 程序来源校验

| 原文 | 冻结版本 | 临时基线Document ID | 原始字节/LF摘要/身份重建 |
| --- | --- | --- | --- |
| [docs/sample_knowledge_base/finance/payment_approval_policy.md](../../docs/sample_knowledge_base/finance/payment_approval_policy.md) | V1 | [1] | PASS |
| [docs/sample_knowledge_base/finance/reimbursement_policy.md](../../docs/sample_knowledge_base/finance/reimbursement_policy.md) | V1 | [2] | PASS |
| [docs/sample_knowledge_base/finance/travel_expense_policy.md](../../docs/sample_knowledge_base/finance/travel_expense_policy.md) | V1 | [3] | PASS |
| [docs/sample_knowledge_base/hr/attendance_policy.md](../../docs/sample_knowledge_base/hr/attendance_policy.md) | V1 | [4] | PASS |
| [docs/sample_knowledge_base/hr/leave_policy.md](../../docs/sample_knowledge_base/hr/leave_policy.md) | V1 | [5] | PASS |
| [docs/sample_knowledge_base/hr/onboarding_checklist.md](../../docs/sample_knowledge_base/hr/onboarding_checklist.md) | V1 | [6] | PASS |
| [docs/sample_knowledge_base/it/device_security_policy.md](../../docs/sample_knowledge_base/it/device_security_policy.md) | V1 | [7] | PASS |
| [docs/sample_knowledge_base/it/gitlab_access_request.md](../../docs/sample_knowledge_base/it/gitlab_access_request.md) | V1 | [8] | PASS |
| [docs/sample_knowledge_base/it/vpn_troubleshooting.md](../../docs/sample_knowledge_base/it/vpn_troubleshooting.md) | V1 | [9] | PASS |
| [docs/sample_knowledge_base/product/customer_feedback_process.md](../../docs/sample_knowledge_base/product/customer_feedback_process.md) | V1 | [10] | PASS |
| [docs/sample_knowledge_base/product/product_faq.md](../../docs/sample_knowledge_base/product/product_faq.md) | V1 | [11] | PASS |
| [docs/sample_knowledge_base/product/release_process.md](../../docs/sample_knowledge_base/product/release_process.md) | V1 | [12] | PASS |

12份来源文件存在，原始字节SHA-256、LF文本SHA-256和document_key独立重建全部匹配。每条跨度还核验文档身份、范围、精确子串和证据摘要；冻结Chunk只用于定位实际承载内容，不读取检索排名来判定金标。完整摘要和偏移在JSON建议文件。

## 38题逐项结论

| 题目 | 问题 | 结论 | 审核理由及完整性 | 建议 |
| --- | --- | --- | --- | --- |
| hr_01 | 员工年假至少要提前几个工作日提交？ | PASS | 年假条款明确至少提前3个工作日并指明HR系统；题目只问提前时限，资格、额度和审批层级无需加入正证据。 | 保留现标注，待用户批准；不自动确认。 |
| hr_02 | 试用期员工每月最多有几天带薪病假，超过的部分怎么办？ | PASS | 同一句同时支持试用期每月最多2天带薪病假以及超出按事假处理，覆盖两项核心事实。 | 保留现标注，待用户批准；不自动确认。 |
| hr_03 | 远程办公每月默认最多几天，超过后由谁审批？ | PASS | 同一句明确每月默认不超过4天、超额由部门负责人审批；不把远程办公混同请假。 | 保留现标注，待用户批准；不自动确认。 |
| hr_04 | 新员工缺少离职证明或学历证明时，需要在到岗后多久补交？ | PASS | 补交条款的材料类型、入职后10个工作日与题目吻合；逾期跟进不是本题必要事实。 | 保留现标注，待用户批准；不自动确认。 |
| hr_05 | 普通岗位和管理岗位的试用期分别多久？ | PASS | 分别明确普通岗位3个月与管理岗位6个月；两个人群和期限均保留，没有相互替换。 | 保留现标注，待用户批准；不自动确认。 |
| finance_01 | 日常费用发生后，报销申请需要在多久内提交？ | PASS | 原文直接规定费用发生后15个工作日；跨月尽量次月5日前和延迟补审批是额外情形，不为本题强加正证据。 | 保留现标注，待用户批准；不自动确认。 |
| finance_02 | 打车报销需要哪些材料？缺少行程单会怎样？ | REVISE | 现有两条准确覆盖打车专用附件及缺少行程单的补交/退回，但“需要哪些材料”未限定专用附件。原文通用报销材料段另有必填信息与附件要求，完整性取决于题意范围，建议先消除歧义。 | 二选一：将问题明确为“打车报销的专用附件是什么，缺少行程单会怎样？”并保留现证据；或保留宽泛材料问题，补充原文通用必填信息及附件段。先由用户确认范围，不直接改旧题。 |
| finance_03 | 单张发票金额超过5000元，需要谁复核？ | PASS | 保留单张发票、超过5000元两个条件，复核者确为财务经理；不混用单笔费用审批规则。 | 保留现标注，待用户批准；不自动确认。 |
| finance_04 | 差旅的一线城市和其他城市住宿标准各是多少？超标需要谁审批？ | PASS | 两条分别覆盖600/450元每晚标准、超标提前说明及部门负责人审批；缺一都会遗漏核心要求。 | 保留现标注，待用户批准；不自动确认。 |
| finance_05 | 供应商单笔付款超过10万元，需要谁审批？ | PASS | 金额分段明确超过10万元由总经理审批；不从其他金额档推断本题不明列的附加审批人。 | 保留现标注，待用户批准；不自动确认。 |
| it_01 | VPN提示720错误应该如何处理？ | PASS | 重启、禁用/启用适配器与仍失败联系IT重装分别有直接证据，处理顺序及失败条件完整。 | 保留现标注，待用户批准；不自动确认。 |
| it_02 | VPN连续输错密码几次会锁定，锁定多久？ | PASS | 明确连续5次锁定30分钟，覆盖全部问题；紧急手动解锁属额外建议，不强制新增证据。 | 保留现标注，待用户批准；不自动确认。 |
| it_03 | GitLab Maintainer权限的审批角色有哪些？我只咨询规则，不发起申请。 | REVISE | e1充分支持直属主管加项目技术负责人审批。e2讲权限授予对象，不证明审批角色；若列为正相关，会把模块负责人等授予对象与审批角色混淆，建议移出本题正证据。 | 保留e1 primary/2；将e2从本题正证据列表移除，按现有未标注结果grade=0处理。不要直接把e2改成数据加载器不接受的role/grade组合，也不要把模块负责人添加成审批人。 |
| it_04 | GitLab临时项目权限默认有效期多久，到期后怎么办？ | PASS | 临时项目权限默认90天以及到期重新申请在同一句，不能扩展成永久权限规则。 | 保留现标注，待用户批准；不自动确认。 |
| it_05 | 办公设备安全规范允许把公司源代码同步到个人GitHub吗？ | PASS | 原文常见问题的Q明确为个人GitHub，A为禁止并限定公司授权平台；已核实Q/A指代。数据安全段另有一般禁令，但无需为同一核心结论机械添加重复证据。 | 保留现标注，待用户批准；不自动确认。 |
| product_01 | 企业知识助手产品FAQ中支持上传哪些文档格式？ | PASS | FAQ原文确实列出Markdown、TXT、PDF及常见文本；这里只审查“FAQ中”的声明，不据此宣称生产前端实际支持PDF。 | 保留现标注，待用户批准；不自动确认。 |
| product_02 | 企业知识助手产品FAQ里，扫描件图片能直接提取文字吗？ | PASS | 保留“无法保证直接提取”而非绝对不能，且明确OCR转换为可复制文本，支持性及措辞强度一致。 | 保留现标注，待用户批准；不自动确认。 |
| product_03 | 版本发布前至少提前多久冻结需求？冻结后允许哪些变更？ | PASS | 冻结提前至少5个工作日、原则不加新需求、仅阻塞级缺陷/合规风险例外均包含在原证据中。 | 保留现标注，待用户批准；不自动确认。 |
| product_04 | 涉及登录的版本发布，灰度比例从多少开始，需要观察多久才能扩大到50%？ | PASS | 原证据覆盖10%、至少2小时、无新增严重告警后到50%；原文同一灰度章节及冻结Chunk还明确登录版本必须灰度，适用条件已核实。无需把已有上下文重复计为额外相关Chunk。 | 保留现标注，待用户批准；不自动确认。 |
| product_05 | 客户反馈被分为P1时，多久给出处理计划，多久同步一次进展？ | PASS | 两个跨度分别支持P1每天同步及3个工作日内给出计划，不能错用P0每2小时规则；无句号的原始跨度仍是合法精确子串。 | 保留现标注，待用户批准；不自动确认。 |
| process_01 | 我是研发部张三，申请VPN权限，权限级别为只读，审批人为李经理，补充信息是远程办公。 | PASS | 这是用户明确发起的VPN申请，人员和业务字段来自用户原话，不由制度补造；无RAG正证据及检索指标N/A设置合理。 | 仅认可检索N/A；不添加制度证据、不校验字段提取。 |
| process_02 | 我是测试部王芳，申请GitLab权限，权限级别为Reporter，审批人为赵主管，补充信息是查看项目A代码。 | PASS | 这是明确GitLab申请而非制度咨询；不把权限制度当作申请人、审批人或期限的原话证据，保持检索N/A。 | 仅认可检索N/A；不添加制度证据、不校验字段提取。 |
| process_03 | 我是张三，申请VPN权限，权限级别为只读，审批人为李经理，补充信息是居家办公。 | PASS | 用户发起VPN申请但缺部门；部门不能从知识库推断，当前无正证据及N/A设置合理。 | 仅认可检索N/A；不添加制度证据、不校验字段提取。 |
| process_04 | 我是产品部陈晨，申请GitLab权限。 | PASS | 用户提出GitLab申请，权限等级和审批人未给出；不能把制度默认Reporter或Developer填入本题RAG金标，保持N/A。 | 仅认可检索N/A；不添加制度证据、不校验字段提取。 |
| process_05 | 请帮我开通VPN账号，我要提交申请。 | PASS | 明确办理账号申请而非查询制度；没有身份和部门，文档不能补出这些用户事实，保持无正证据及检索N/A。 | 仅认可检索N/A；不添加制度证据、不校验字段提取。 |
| refusal_01 | 星辰科技2027年度董事长奖金的准确金额是多少？ | PASS | 完整12文档未给出2027年度董事长奖金数额；仅可确认冻结语料内无正证据，不能推断现实中没有奖金。 | 保留冻结12文档内无正证据；不据此推断现实事实或拒答输出正确。 |
| refusal_02 | 财务报销制度规定的公司开票税号具体是哪一串数字？ | PASS | 报销FAQ要求发票税号清晰完整，但没有公司开票税号的实际数字；字段要求不是字段值的证据。 | 保留冻结12文档内无正证据；不据此推断现实事实或拒答输出正确。 |
| refusal_03 | 公司VPN服务器的公网IP地址具体是多少？ | PASS | VPN文档有内网测试域名，但没有VPN服务器公网IP；不能把域名当作IP或执行DNS查询补造证据。 | 保留冻结12文档内无正证据；不据此推断现实事实或拒答输出正确。 |
| refusal_04 | 员工年假未休完时，每天折算现金的具体金额是多少？ | PASS | 请假文档有年假天数与保留到次年3月31日的规则，没有未休年假现金折算日金额；不能由额度推算现金。 | 保留冻结12文档内无正证据；不据此推断现实事实或拒答输出正确。 |
| refusal_05 | 财务报销系统的管理员账号和初始密码分别是什么？ | PASS | 报销、账号及安全材料未列财务报销系统管理员账号和初始密码；VPN/邮箱关系不支持猜测另一系统凭据。 | 保留冻结12文档内无正证据；不据此推断现实事实或拒答输出正确。 |
| holdout_hr_01 | 员工忘打卡后多久内补卡，每月最多补几次，超额由谁审批？ | PASS | 两条覆盖2个工作日、每月3次及超额部门负责人审批，都是问题所需。FAQ的主管确认是其他流程细节，不追加为本题必要证据。 | 保留现标注，待用户批准；不自动确认。 |
| holdout_finance_01 | 没有合同时，2500元的小额采购是否还有申请途径，需要哪位负责人审批？ | PASS | 原文FAQ的问题明确限定无合同小额采购，A给出不超过3000元可提交采购说明和付款申请、部门负责人审批，2500元符合范围。一般付款前置条件作为辅助边界有价值；不能据此声称采购/预算等全部依据均被免除。 | 保留现标注，待用户批准；不自动确认。 |
| holdout_it_01 | VPN短信验证码收不到有什么替代办法，换手机号又该先在哪里更新？ | PASS | 切换身份验证器动态码、先HR更新手机号再IT同步两个事实均直接且有完整顺序，不因出现HR关键词改变来源归属。 | 保留现标注，待用户批准；不自动确认。 |
| holdout_product_01 | 产品发布前的回滚方案要写什么，出现哪些情况必须立即回滚？ | PASS | 方案四要素和P0/核心链路不可用/错误数据写入时立即回滚均有直接证据；不外推未列出的回滚情形。 | 保留现标注，待用户批准；不自动确认。 |
| holdout_cross_01 | 外出拜访客户的申请需要填什么，出差结束报销又需要哪些材料？ | PASS | 外出申请的四字段来自考勤原文，出差报销五类材料来自差旅原文；两个子问有独立主要证据，不拿同文件命中替代跨文档完整性。 | 保留现标注，待用户批准；不自动确认。 |
| holdout_cross_02 | 临时只查看代码该用哪级GitLab权限，公司源代码能否上传个人网盘？ | PASS | Reporter满足只查看/下载代码，数据安全条款明确禁止个人网盘；两来源各支持一个子问，等级2均合理。 | 保留现标注，待用户批准；不自动确认。 |
| holdout_refusal_01 | 公司2026年度净利润的具体金额是多少？ | PASS | 12份制度和产品说明均未提供2026年度净利润；付款金额阈值不是公司的净利润值，保持语料内无正证据。 | 保留冻结12文档内无正证据；不据此推断现实事实或拒答输出正确。 |
| holdout_refusal_02 | 请提供全体员工身份证号码的完整清单。 | PASS | 入职清单要求员工提交身份证明，但没有全体员工实际身份证号码；材料名称不能变成具体号码清单。 | 保留冻结12文档内无正证据；不据此推断现实事实或拒答输出正确。 |

## 37条原始证据逐条建议

| 证据ID | 原始状态 | 原角色/等级 → 建议 | 来源/偏移 | 支持性理由 | 修改建议 |
| --- | --- | --- | --- | --- | --- |
| hr_01:e1 | pending_review | primary/2 → primary/2 | PASS，leave_policy.md [251,278) | 直接给出年假提前3个工作日及HR系统。 | 无需必要修改。 |
| hr_02:e1 | pending_review | primary/2 → primary/2 | PASS，leave_policy.md [407,438) | 同句支持2天带薪与超过按事假两事实。 | 无需必要修改。 |
| hr_03:e1 | pending_review | primary/2 → primary/2 | PASS，attendance_policy.md [384,416) | 同时给出默认4天及超额部门负责人审批。 | 无需必要修改。 |
| hr_04:e1 | pending_review | primary/2 → primary/2 | PASS，onboarding_checklist.md [531,566) | 材料缺失情形与入职后10个工作日明确。 | 无需必要修改。 |
| hr_05:e1 | pending_review | primary/2 → primary/2 | PASS，onboarding_checklist.md [460,488) | 普通岗3个月和管理岗6个月分别明确。 | 无需必要修改。 |
| finance_01:e1 | pending_review | primary/2 → primary/2 | PASS，reimbursement_policy.md [74,107) | 明确费用发生后15个工作日，不是自然日。 | 无需必要修改。 |
| finance_02:e1 | pending_review | primary/2 → primary/2 | PASS，reimbursement_policy.md [590,616) | 对应原文打车缺行程单问答，直接支持补充、无法补充退回。 | 保留原文、role和等级；材料范围待case级澄清。 |
| finance_02:e2 | pending_review | primary/2 → primary/2 | PASS，reimbursement_policy.md [250,267) | 直接支持电子发票与行程单两项打车专用附件；不单独声称完整列尽所有通用材料。 | 保留原文、role和等级；材料范围待case级澄清。 |
| finance_03:e1 | pending_review | primary/2 → primary/2 | PASS，reimbursement_policy.md [389,415) | 单张发票超过5000元对应财务经理复核。 | 无需必要修改。 |
| finance_04:e1 | pending_review | primary/2 → primary/2 | PASS，travel_expense_policy.md [336,378) | 一线600元、其他450元及每晚条件齐全。 | 无需必要修改。 |
| finance_04:e2 | pending_review | primary/2 → primary/2 | PASS，travel_expense_policy.md [378,405) | 超标需提前说明与部门负责人审批，不能省略提前条件。 | 无需必要修改。 |
| finance_05:e1 | pending_review | primary/2 → primary/2 | PASS，payment_approval_policy.md [266,288) | 超过10万元明确由总经理审批。 | 无需必要修改。 |
| it_01:e1 | pending_review | primary/2 → primary/2 | PASS，vpn_troubleshooting.md [242,276) | 给出重启及网络适配器禁用/启用顺序。 | 无需必要修改。 |
| it_01:e2 | pending_review | primary/2 → primary/2 | PASS，vpn_troubleshooting.md [276,307) | 仍无法恢复才联系IT重装VPN客户端。 | 无需必要修改。 |
| it_02:e1 | pending_review | primary/2 → primary/2 | PASS，vpn_troubleshooting.md [130,159) | 5次、30分钟和连续输错条件齐全。 | 无需必要修改。 |
| it_03:e1 | pending_review | primary/2 → primary/2 | PASS，gitlab_access_request.md [309,350) | 准确列出直属主管与Maintainer额外技术负责人审批。 | 无需必要修改。 |
| it_03:e2 | pending_review | auxiliary/1 → 移出正证据（既有未标注grade=0） | PASS，gitlab_access_request.md [176,206) | 权限授予对象不是审批角色；该跨度不能证明谁审批本次申请，易误把模块负责人作为审批人。 | 从本题正证据移除，未标注结果按现有grade=0计；可保留为不计分背景备注。 |
| it_04:e1 | pending_review | primary/2 → primary/2 | PASS，gitlab_access_request.md [441,469) | 同时给出临时权限90天与到期重新申请。 | 无需必要修改。 |
| it_05:e1 | pending_review | primary/2 → primary/2 | PASS，device_security_policy.md [526,561) | 已核实紧邻Q为个人GitHub；A的禁止及授权平台限制支持本题。 | 无需必要修改。 |
| product_01:e1 | pending_review | primary/2 → primary/2 | PASS，product_faq.md [169,201) | 原文直接列出四种格式/内容类别；按FAQ声明审核。 | 无需必要修改。 |
| product_02:e1 | pending_review | primary/2 → primary/2 | PASS，product_faq.md [238,283) | 扫描件无法保证直接提取及OCR转换要求都存在。 | 无需必要修改。 |
| product_03:e1 | pending_review | primary/2 → primary/2 | PASS，release_process.md [107,165) | 冻结时限与允许变更的两类例外完整。 | 无需必要修改。 |
| product_04:e1 | pending_review | primary/2 → primary/2 | PASS，release_process.md [349,403) | 10%、至少2小时及无新增严重告警后扩至50%，不是只需等待时间。 | 无需必要修改。 |
| product_05:e1 | pending_review | primary/2 → primary/2 | PASS，customer_feedback_process.md [498,513) | P1为每天同步，不是P0每两小时；精确子串无需补句号才能有效。 | 无需必要修改。 |
| product_05:e2 | pending_review | primary/2 → primary/2 | PASS，customer_feedback_process.md [261,296) | P1处理计划时限为3个工作日，不混同当天响应。 | 无需必要修改。 |
| holdout_hr_01:e1 | pending_review | primary/2 → primary/2 | PASS，attendance_policy.md [245,284) | 明确2个工作日内HR系统补卡及说明原因。 | 无需必要修改。 |
| holdout_hr_01:e2 | pending_review | primary/2 → primary/2 | PASS，attendance_policy.md [284,316) | 每月3次及超额部门负责人审批齐全。 | 无需必要修改。 |
| holdout_finance_01:e1 | pending_review | primary/2 → primary/2 | PASS，payment_approval_policy.md [554,597) | 来自无合同小额采购FAQ的A，2500不超过3000；已在原文和冻结Chunk中核实Q限定无合同。 | 保留；可选用完整Q/A替换但不重复新增证据。 |
| holdout_finance_01:e2 | pending_review | auxiliary/1 → auxiliary/1 | PASS，payment_approval_policy.md [78,105) | 一般付款依据限制与当前无合同例外相邻语义有补充价值；只作边界背景，不取消FAQ具体途径。 | 无需必要修改。 |
| holdout_it_01:e1 | pending_review | primary/2 → primary/2 | PASS，vpn_troubleshooting.md [451,475) | 短信收不到可切换身份验证器动态码。 | 无需必要修改。 |
| holdout_it_01:e2 | pending_review | primary/2 → primary/2 | PASS，vpn_troubleshooting.md [475,517) | 手机号先HR更新、后IT同步的顺序完整。 | 无需必要修改。 |
| holdout_product_01:e1 | pending_review | primary/2 → primary/2 | PASS，release_process.md [426,459) | 回滚触发、负责人、预计耗时、兼容风险四项完整。 | 无需必要修改。 |
| holdout_product_01:e2 | pending_review | primary/2 → primary/2 | PASS，release_process.md [459,494) | 三类触发情形及立即启动回滚完整。 | 无需必要修改。 |
| holdout_cross_01:e1 | pending_review | primary/2 → primary/2 | PASS，attendance_policy.md [494,516) | 外出申请地点、时间、事项、同行人员四字段完整。 | 无需必要修改。 |
| holdout_cross_01:e2 | pending_review | primary/2 → primary/2 | PASS，travel_expense_policy.md [518,551) | 出差申请单、交通票据、住宿发票、打车行程单、说明五类齐全。 | 无需必要修改。 |
| holdout_cross_02:e1 | pending_review | primary/2 → primary/2 | PASS，gitlab_access_request.md [711,742) | 只查看或下载代码对应Reporter，无需编造更高权限。 | 无需必要修改。 |
| holdout_cross_02:e2 | pending_review | primary/2 → primary/2 | PASS，device_security_policy.md [280,327) | 明确禁止个人网盘，含源代码对象及否定词。 | 无需必要修改。 |

## 需要重点确认的两项修订

### finance_02：先确定“材料”的范围

二选一：将问题明确为“打车报销的专用附件是什么，缺少行程单会怎样？”并保留现证据；或保留宽泛材料问题，补充原文通用必填信息及附件段。先由用户确认范围，不直接改旧题。

若只考查打车专用附件及缺行程处理，原两条证据已覆盖核心答案；建议明确题干即可。若保持“完整报销材料”的宽范围，以下原文可作主要证据，不是凭空补充：

> 报销申请必须包含费用说明、费用发生日期、金额、费用类型和项目归属。发票、付款截图、消费明细和审批截图需要作为附件上传。

来源 docs/sample_knowledge_base/finance/reimbursement_policy.md [189,248)，程序校验PASS；这是条件性建议，未加入原集。程序映射确认该段与现有专用附件证据同属冻结Chunk 3；没有新增必要Chunk，不据此宣称召回提升或重新计分。

### it_03：授予对象不能代替审批角色

保留e1 primary/2；将e2从本题正证据列表移除，按现有未标注结果grade=0处理。不要直接把e2改成数据加载器不接受的role/grade组合，也不要把模块负责人添加成审批人。

e2原文正确，但“项目技术负责人或模块负责人”是授予对象限制，并未证明模块负责人拥有审批角色。保留e1即可支持本题审批核心；背景信息可以保留为不计分备注。该建议依据语义角色区别，不依据辅助块是否被Keyword召回。

## 适用条件及可选增强（不强制扩充正证据）

- product_04：已核实登录版本必须先灰度。原证据已完整支持10%、至少2小时和无新增严重告警；同一冻结Chunk包含适用前置句，题目PASS。可选补前置句以使引文更自包含，不新增必要Chunk。
- holdout_finance_01：已核实无合同限定来自原文Q；2500元落在不超过3000元范围，仍需部门负责人审批。通用付款依据可保留为辅助边界，不能回答成免除全部合同/采购/预算限制。可选将e1扩为完整Q/A，不重复累计为另一条独立正证据。

**product_04 可选原文：**

> 涉及核心交易、权限、账单、登录和数据迁移的版本必须先灰度发布。

来源 docs/sample_knowledge_base/product/release_process.md [318,349)，程序校验PASS；原集未改变。

**holdout_finance_01 可选原文：**

> Q：没有合同但需要小额采购怎么办？  
> A：单笔不超过 3000 元的小额采购可以提交采购说明和付款申请，但仍需要部门负责人审批。

来源 docs/sample_knowledge_base/finance/payment_approval_policy.md [532,597)，程序校验PASS；原集未改变。

## 无证据题的审核范围

流程5题是办理请求，不能由知识库补出姓名、部门或审批人，所以保留process_not_applicable。7道无正证据题在12份原文中均没有所求奖金、税号、公网IP、现金折算、管理员凭据、净利润或身份证号码清单；相关词、材料名称或内网域名不能代替这些具体事实。PASS只确认有限冻结语料内的分类，不表示现实世界没有该事实，也不替代答案级拒答复核。

## 后续动作

用户只需重点确认finance_02的范围、it_03的辅助证据去留，以及holdout_finance_01不泛化付款豁免的表述；不需要重新逐题阅读JSON。确认前本报告只是建议，不应用补丁、不改pending_review、不重算历史基线。保留集同样完成审核，但不据其检索结果调整算法。

结构化原始证据、逐条校验、建议等级和候选跨度见 [ai_review_candidates.json](ai_review_candidates.json)。
