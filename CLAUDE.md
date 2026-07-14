# Maincode — AI Software Engineer (Back End) 面试准备记忆

**当前状态**（2026-07-14周二凌晨建档，周二下午2:27再次更新）：**面试确定为2026-07-16周四 9:00-9:15am AWST（珀斯本地时间，跟用户所在时区一致，不用换算），面试官Kim，Google Meet视频通话，标题"First Chat with Maincode"**。用户Python水平："写过Python后端API(FastAPI/Flask一类)，但没做过模型推理/GPU serving"。可用准备时间：周二/周三**白天**（平时9:00起床），**周二和周三晚上5-10点都不可用**。**周四这天例外——面试9点整开始，跟平时起床时间撞上了，必须提前到7:30起床，见五节时间表**。**Amana Living二面已经面完，MainCode现在是唯一重心**。

**重大进展（周二下午2:27）**：**Aurora 的 Phase 1 已经完整写完，并且真的接上了 Docker Compose + 真实 Postgres/Redis/Ollama 端到端验证通过**——不是玩具项目，是一个能流式跑通真实模型推理、有认证/限流/重试/持久化/可观测性的完整后端服务，过程中还抓到并修好了 4 个只有真实环境才会暴露的 bug（细节见 Aurora 仓库的 `docs/DEVLOG.md`）。这直接让原计划周二/周三"从零搭一个玩具 FastAPI 项目"的 Block 2a/2b/周三Block1 变得多余——那些环节想练的东西（streaming endpoint、rate limiter、retry/backoff、认证中间件），Aurora 里都有生产级别的真实实现，直接拿来当例子讲，分量比临时搭的玩具项目高得多。省下来的时间改成"按 `docs/study/PHASE1_STUDY_GUIDE.md` 的顺序，一个模块一个模块吃透 Aurora 的代码"，这是本次时间规划调整的核心依据，详见五节；**Phase 2/3/4 的决定是：这次周四之前不写、不学**，理由也在五节说明。

---

## 一、面试流程全貌（来自Glassdoor，2026-07核实）

Maincode是11-15名工程师的小团队，从零训练自己的大语言模型"Matilda"。Glassdoor两条评论一致描述流程：

**HR/初筛screen(15分钟)** → **behavioral interview** → **一周不带薪take-home项目** → **全team(可能含CEO)现场presentation**

一条负面评论明确吐槽："15分钟screen没有任何follow-up问题，之后却要求一周无偿工作+全team presentation，投入产出不对等"。另两条员工评价强调文化是"cutting edge/fast-paced/high agency，不适合把这当朝九晚五的人"。

**现实认知**：这周四的15分钟只是流程第一关，通常不会深挖技术细节（"zero follow-up questions on background"是原话）。真正的技术含量在behavioral之后的take-home。**不要在周二/周三把所有时间都砸在准备15分钟的话术上**——更值得投入的是为后续技术环节打地基的真实学习，这也是用户自己提出的诉求："希望准备过程中能学到东西"。

已知会被问到的问题类型（来自Glassdoor真实面经）：
- Walk me through your relevant work experience
- Why do you want to join Maincode?
- What was the scale of your past AI projects (e.g., number of end-users)?
- Do you have direct experience working with GPUs?
- Have you done end-to-end full-stack development work?

---

## 二、岗位核心理解（JD逐句拆解，别被"AI Software Engineer"这个title误导）

Maincode训练并运营自研LLM"Matilda"，这个岗位是**model-serving/生产推理基础设施的后端工程师**，**不是**RAG应用开发、不是前端、不是prompt engineering、不是套壳调用OpenAI/Claude这类"third-party AI服务"的工作——JD原文明确写了"It is not integrating third party AI services or building on top of external models"，这一点跟May过去的MBSPro工作（正是调OpenAI/Mistral API）在性质上是不同的工作。

**核心工作内容**（JD"This Includes Work Such As"逐条）：
- 处理模型推理和用户请求的服务
- 管理请求/会话/流式响应(streaming responses)的系统设计
- 可靠性机制：rate limiting、retries、graceful failure
- 面向公开访问的认证与访问控制
- 日志、遥测(telemetry)、评估信号的系统设计
- 提升模型serving的延迟/吞吐/可靠性
- 把新的模型checkpoint集成进生产系统
- 和training/infra工程师紧密协作部署、运营模型

**画像要求**："早期职业(1-2年经验)、好奇心强、愿意学习大规模系统实际怎么运作"。**关键原文，可以直接作为回答gap问题的依据**："You do not need prior experience serving large language models. You do need the discipline to build systems that are hard to break." —— 官方说了不需要LLM serving经验，需要的是"系统很难被弄坏"的工程纪律。

### 2.1 Maincode真实技术版图（用户2026-07-14自行调研补充，来源：官网/研究博客/AMD官方案例研究/Forbes Australia/ACS Information Age/招聘页，比Glassdoor那点信息扎实得多）

**核心结论**：Maincode不是套壳调用别人模型的AI应用公司，是真的自己从零训练模型+自建GPU集群+自己写推理引擎，再包装成产品。JD里"we design the architectures, run the infrastructure, train the models, and operate the systems ourselves"不是营销话术，是真的在做。

- **Matilda**：旗舰对话式AI，2025年10月SXSW Sydney首次公开演示(当时版本围绕AFL/AFLW数据)，2026年5月仍在招募beta用户。**参数规模/训练token数/完整benchmark全部没有公开，网上任何具体数字都不要当成事实引用或在面试里主动说出来**——说错了比不知道更扣分。
- **Maincoder-1B**：2025年12月发布，1B参数代码模型，HumanEval 76%，Apache 2.0开源，权重挂在Hugging Face——这是目前最实锤的"他们真的能训练+发布模型"的证据，比Matilda本身更适合拿来当具体例子引用（因为它是唯一有公开benchmark的）。
- **MC-2**：3000万澳元的GPU集群("AI factory")，用AMD Instinct MI355X(不是NVIDIA)，位于墨尔本。选AMD的理由是同预算下GPU数量接近两倍、显存更大(288GB HBM3E)。训练路径是先在NVIDIA上练早期模型，再把训练栈移植到AMD验证，最后部署MI355X集群——**GPU总数没有公开**。
- **自研Rust推理引擎**（目前公开信息里技术含量最高的一块）：不用vLLM/SGLang/ROCm这些现成框架，直接写Rust通过Linux kernel driver驱动AMD GPU，自己实现tensor parallel all-reduce、paged KV cache、低精度decode，已经能完整跑通Qwen3-235B-A22B这个MoE模型的decoder作为验证基准（这只是拿来测试推理系统的参照模型，不代表Matilda本身是235B参数）。
- **MCX**：面向银行/保险等受监管企业的"企业AI操作系统"，让客户在自己环境里跑一个"迷你版Maincode集群"，卖点是数据不出客户环境、可审计、避免持续付海外模型API的钱。
- **创始人Dave Lemphers**：连续创业者，Microsoft Azure早期工程背景+法学学位，风格是hands-on systems founder，亲自写GPU kernel/推理引擎/服务器设计的技术博客，强调first principles、不满足于套用现成框架、对工程师要求高ownership——**这大概率就是周四这场面试乃至后续技术面背后的文化基调**。
- **主要投资人Ed Craven**（Stake/Kick联合创始人）是majority investor，资金集中于一人而非VC分批融资，能承担早期难盈利的底层研发投入。

**对你这个岗位的重要澄清**：JD里"AI Software Engineer (Back End)"大概率是坐在这个自研Rust推理引擎"上层"的Python服务(request/session/streaming/rate limiting/auth/logging)，跟真正写GPU kernel的是不同岗位——招聘页上"AI Infrastructure Engineer"是单独的title。**这其实略微缩小了gap焦虑**：你不需要去懂Rust/GPU kernel细节，需要懂的是"怎么设计一个可靠地把请求转发给推理引擎、处理流式输出、限流重试"的服务——跟五节FastAPI玩具项目的方向完全对得上，说明学习计划没选错方向。

---

## 三、诚实差距评估

**可迁移的强项**（面试时可以诚实地positive frame）：
- TypeScript全栈生产经验：NestJS写过REST服务(`claim.service.ts`)、独立设计过带重试/失败处理的pipeline(MBSPro的`agent-graph.service.ts`)——这本质上就是"设计一个有边界条件、可控终止的状态机"，跟这个岗位要的"reliability mechanism设计"思维方式是通的
- BeeQuant的Market Overview Service：调用两个外部REST API、显式处理HTTP 429限流、结构化错误码而非裸异常——这是最接近JD"rate limiting/graceful failure/APIs"这几个词的真实经历，虽然不是模型推理场景，但错误处理的思维模型是一样的
- 三态验证设计(PASS/SOFT/FAIL)体现的是"系统在不确定信息下该怎么表现得可预测、可审计"——这跟JD说的"stay calm and methodical when production behaves unexpectedly"是同一类工程气质
- Zustand session bug的debug经历：体现的是"理解状态在系统里何时被谁覆盖"的直觉，这类时序/状态思维在并发请求处理里同样重要

**真实的差距，不要假装没有**：
1. 没有用Python做过上线的生产服务（写过但没上线过）
2. 模型推理/GPU serving经验为零（vLLM/TGI/continuous batching/KV cache这些词目前只是听过，没实操过）
3. Streaming responses、rate limiting、retry在真实生产系统里的实操经验为零（有类似心智模型，但没写过这类代码）

**结论**：这场面试的定位不是"包装成已经有这个背景"，而是"诚实地展示可迁移的工程思维 + 真实的、这几天正在发生的学习行动"。JD自己都说不需要LLM serving经验——这是可以坦然承认gap、同时用具体行动(下面的学习计划)证明学习速度的最佳场景。

---

## 四、周四15分钟HR screen准备重点

15分钟的screen性质是motivation/fit/logistics，不会深挖技术（参考Glassdoor原话"zero follow-up questions"）。需要准备：

1. **30-45秒版自我介绍**（不是MBSPro那套2分钟版本——那套强调RAG/frontend，这里要重新调焦到backend/系统可靠性关注点，弱化"前端/RAG应用"标签，强调"我喜欢往下钻系统怎么在负载下工作、怎么设计容错"这条线）
2. **"Why Maincode"**——需要基于对Matilda/Maincode实际在做什么的了解来回答，不能只说"AI很酷"。可以提：从零训练自己的模型、自建compute cluster、"build AI systems end to end"这个理念，跟"只会调别人API"的公司比更接近系统本质
3. **Gap桥接话术**（针对"有没有GPU经验"这类问题）：诚实说没有直接GPU/模型推理经验，但①JD本身说明不需要 ②这周已经在主动学习(具体到下面的学习计划里做了什么) ③过去的可迁移经验(rate limiting/错误处理/状态机设计)
4. **Logistics确认**：工作签证/工作权限(JD写明"not able to offer visa sponsorship, applicants must have existing and unrestricted work rights"——需要跟用户确认简历上的身份状态是否满足)、Melbourne on-site(是否方便通勤/是否需要搬家)、能否接受后续"behavioral+一周take-home+全team presentation"这个流程
5. **反问HR的问题**：已经用2.1节的真实调研升级成更具体的版本，直接看4.1⑤定稿，这里不重复（避免两处内容不同步）。

### 4.1 周四Cheat Sheet草稿（周二Block 4去改成定稿，周三Block 2大声练，周四早上只看这个，不看长文档）

**① 30-45秒自我介绍（英文，草稿，周二可以照这个改）**：
> "Hi, I'm May. I'm a full-stack engineer, but the part of the work I care most about is the backend — designing systems that stay reliable under load and fail gracefully instead of failing silently. In my last project I built a retrieval pipeline with a bounded retry loop and a rules-based verification layer that had to behave predictably even when the input was messy or incomplete. I'm looking to move further into that kind of systems-focused backend work, which is part of why this role caught my attention."

**② Why Maincode（已用2.1节的真实调研填好，周二Block 1不用重新查了，直接跳过）**：
> "What stands out to me is that Maincode isn't wrapping someone else's model — they train their own models from scratch and recently open-sourced Maincoder-1B to prove that pipeline out. They've also built their own Rust inference engine on their AMD GPU cluster instead of relying on existing frameworks, which tells me they care about owning the full stack end to end, not just shipping a product on top of someone else's API. That kind of first-principles, full-stack approach is exactly the environment I want to be in."

**③ Gap桥接话术（被问GPU/模型推理经验时用，英文，已经可以直接用）**：
> "I haven't directly worked with GPU serving or LLM inference infrastructure yet — the job post was actually upfront that this isn't required. What I do have is experience designing systems with explicit failure handling: retry logic, rate limiting, structured error codes. And this week I've been building a small FastAPI service that mirrors a few of the patterns in this role — a streaming endpoint, a rate limiter, and retry-with-backoff — specifically to get hands-on with the vocabulary before day one."

**④ Logistics答案（占位，周三Block 3自己确认后填，这几个是你自己要想清楚的，我答不了）**：
> - 工作权限：[TODO：确认简历上的身份是否满足"unrestricted work rights, no visa sponsorship"]
> - Melbourne on-site：[TODO：能不能通勤/是否要搬家，怎么回答]
> - 能否接受"behavioral+一周take-home+全team presentation"流程：[TODO：想清楚自己的底线]

**⑤ 反问问题（2026-07-14用2.1节真实调研升级过，比原来的通用版本更具体，直接用）**：
> - "I saw you've built a custom Rust inference engine on the MI355X cluster instead of using something like vLLM — for this role specifically, would I be working more on the layer above that (session/streaming/request handling), or closer to the engine itself?"
> - "For integrating new checkpoints of Matilda into production, what does that release cadence look like right now?"
> - "Out of the roles currently open, how many are already on the backend/serving side versus research?"

---

## 五、学习计划（这才是准备过程里真正长脑子的部分，为behavioral/技术面/take-home打地基）

原则：**不要只背话术，要动手做一点东西**——这样"这周我在学/做什么"才是真实、经得起追问的故事，而不是临时编的。目标不是变成专家，是建立够用的词汇量+直觉+一个可以拿出来讲的小项目。

**下面每天都是"表格+紧接着的详细说明"成对出现，表格里的Block编号和下面说明的编号一一对应，不需要跳去别的章节找解释**（唯一例外：cheat sheet正文在4.1节，因为它同时被周二/周三/周四三天用到，放这里避免三份不同步）。

### 周二（下午2:27重排——上午的原计划已被 Aurora 的实际进展取代）

| 时间 | 内容 |
|---|---|
| 9:00–14:24 | ~~原Block 1/2a/2b~~ **已被 Aurora 的实际进展取代**——这段时间用来把 Aurora 推进到 Phase 1 完整实现，并接上真实 Docker/Postgres/Redis/Ollama 端到端验证，比原计划的玩具项目价值高得多，不需要回头补 |
| 14:24–16:30 | **Block A**：跟着 `docs/study/PHASE1_STUDY_GUIDE.md` 学 Aurora 代码，覆盖一~三节 |
| 16:30–17:00 | **Block B**：LLM serving概念阅读（原Block 3，内容不变） |
| 17:00–22:00 | 不可用 |
| 22:00后 | 不再学新内容，休息 |

**Block A（约2小时）——Aurora Phase 1 学习，一~三节**：打开 `docs/study/PHASE1_STUDY_GUIDE.md`，按顺序看**一**（全局认知与架构总览，只看设计文档不看代码，重点是 `ARCHITECTURE.md` 和 `PRD.md` 第7节的权衡取舍）、**二**（`shared/`，错误码体系+跨进程数据模型）、**三**（`providers/`，Protocol 类型陷阱+一个接真实 Ollama 才炸出来的真实 bug）。代码里紫色高亮的 `【卷 X.X】` 注释是专门写给你的中文导读，编号跟指南完全对应，跟着走就行，不用逐行读完整个文件。如果读中文吸收更快，`docs/study/zh/` 下有对应的设计文档中文翻译，可以对照看。

**Block B（30分钟）——LLM serving概念阅读，不写代码**：跟原计划一样，直接读 Maincode 自己研究博客(blog.maincode.com)上那篇讲他们自研 Rust 推理引擎的文章。留意：**KV cache**（为什么生成第二个 token 不用把前面全部重新算一遍）、**tensor parallel/all-reduce**（多张 GPU 怎么协同算一个大模型）、**低精度 decode**。读完之后可以多讲一层：Aurora 里 `ModelProvider` 这个接口存在的理由，就是"上层服务不该关心底层推理引擎具体是什么"，这跟 Maincode 自己把 Rust 推理引擎和上层服务拆开的做法是同一个思路——这个类比是这周新长出来的、真实的观察，不是背出来的。

Cheat sheet（4.1节）内容凌晨已经写好、可以直接用，今天不需要再单独排时间去改。

### 周三

| 时间 | 内容 |
|---|---|
| 9:00–9:30 | 起床 |
| 9:30–12:00 | **Block C**：Aurora 学习，四~五节（`app/core/` 基础设施层 + `app/services/` 编排层） |
| 12:00–12:30 | **Block D**：JWT签名算法(HS256 vs RS256)，直接拿 Aurora 的 `app/core/auth.py` 当例子讲 |
| 12:30–13:30 | 午饭 |
| 13:30–15:30 | **Block E**：Aurora 学习，六~七节（`worker/` 推理引擎，全场分量最重的一段 + `app/api/` 核心链路） |
| 15:30–16:00 | **Block F**：Aurora 学习，八节（测试体系 + DEVLOG 踩坑复盘），学习指南到此收尾 |
| 16:00–16:45 | **Block G**：大声练习4.1节cheat sheet，掐时间 |
| 16:45–17:00 | **Block H**：确认logistics答案+反问问题最终定稿（如果还没定） |
| 17:00–22:00 | 不可用 |
| 22:00后 | 早点休息——**周四要7:30起床，比平时早1.5小时，今晚提前睡** |

**Block C（2.5小时）——Aurora 学习，四~五节**：**四**（`app/core/`：配置、数据库连接层——这里埋了一个"为什么全局单例 engine 对 API 进程是对的、对 worker 进程是错的"的伏笔，先记住这个前提，六节会用到；认证——SHA-256 vs bcrypt 的取舍；限流——滑动窗口日志算法；日志与指标——TTFT/TPOT 这两个指标是"这是真正的推理服务监控"而不是"随便套个 /metrics"的关键证据）、**五**（`app/services/`：会话服务的缓存策略 + 队列桥接，这是全项目最容易出竞态条件的一处代码，值得精读）。

**Block D（30分钟）——JWT签名算法**：内容跟原计划一样（HS256 对称/共享密钥 vs RS256 非对称/公私钥的区别，reset password token 为什么要设过期时间、最好一次性失效），但现在多了一个真实例子可以用：Aurora 的 `app/core/auth.py` 里实打实用了 HS256 签发 JWT，同时用 SHA-256（不是 bcrypt）给 API key 做哈希——后面这个决策本身就是一个能现场讲清楚"为什么"的好故事（高熵密钥 vs 低熵密码的区别），比空背算法定义更有说服力。目标是讲到能脱口而出，不要再出现"I think it's RSA but I'm not sure"这种回答（详细背景见六节）。

**Block E（2小时）——Aurora 学习，六~七节**：**六**（`worker/`：Celery 配置里两个必须显式打开的开关、一个只有真起 worker 进程才会暴露的任务注册坑、可靠性机制、以及全项目技术含量最高的一段——`worker/tasks.py` 里那个跨事件循环的 Postgres/Redis 连接 bug，建议这段花最多时间，练到能完整讲出"现象→根因→为什么单测测不出来→怎么修的"）、**七**（`app/api/chat.py`：核心链路串联，前面几节看的所有零件在这里被组装成一次完整的 `POST /chat` 请求，对照 `SEQUENCE_DIAGRAMS.md` 第1节逐行核对）。

**Block F（30分钟）——Aurora 学习，八节收尾**：测试体系设计思路（`tests/conftest.py` 里那个自己也踩过坑的测试替身）+ `docs/DEVLOG.md` 里"Issues encountered and how they were resolved"那个按 A-E 分类的 15 条问题汇总——这是整个学习计划含金量最高的收官材料，尤其是 D 组那 4 个"只有真实环境才能抓到的 bug"，每一条都练到能讲出来。

**Block G（45分钟）——大声练习cheat sheet**：把4.1节写好的自我介绍/why Maincode/gap桥接对着空气大声说一遍，掐表计时。**15分钟的电话不是让你讲2分钟的完整故事，每段话要短、清楚、有信息量**——如果自我介绍说出来超过1分钟，回去精简。

**Block H（15分钟）——logistics自己确认+反问问题定稿**：这一步是你自己要想清楚、我答不了的部分：工作权限是否满足JD要求的"unrestricted work rights"、Melbourne on-site通勤/搬家怎么办、能不能接受后面"behavioral+一周take-home+全team presentation"这个流程。想清楚后填进4.1节④的`[TODO]`。反问问题如果周二没定稿完，这里最终定稿。

**关于 Aurora 的 Phase 2/3/4：这次周四之前刻意不写、不学。** 理由：①周四这15分钟是低风险的 motivation/fit screen，Glassdoor 原话是"zero follow-up questions on background"，不会深挖到 Phase 2 的熔断器/背压这种细节；②真正会深挖技术的环节是 behavioral interview 之后的一周 take-home，现在还没排期，到时候再投入 Phase 2/3/4 时效性更强、记忆也更新鲜；③比起囫囵吞枣多学几个还没验证过的 phase，把 Phase 1（已经完整实现+真实验证过的部分）讲透，是这两天时间投入产出比更高的选择——面试官更容易被"一个人能把自己做过的东西讲清楚"打动，而不是"读过但没消化的广度"。如果周三 Block C-F 提前完成、还有余裕，可以把多出来的时间用来重新过一遍已经学过的模块、练习口头复述，而不是急着往下学 Phase 2。

### 周四（面试确定：2026-07-16 9:00-9:15am AWST，面试官Kim，Google Meet，"First Chat with Maincode"）

| 时间 | 内容 |
|---|---|
| 7:30 | 起床(比平时早1.5小时，这天必须早起) |
| 7:30–7:50 | 洗漱、清醒、喝点东西 |
| 7:50–8:15 | 过一遍4.1节cheat sheet，不重看长文档 |
| 8:15–8:35 | 测试Google Meet链接能进、摄像头麦克风正常、光线/背景/网络都确认好 |
| 8:35–8:55 | 留白缓冲，喝水、深呼吸 |
| 8:55 | 提前进入Meet等候 |
| **9:00–9:15** | **面试(Kim)** |
| 面试后 | 5分钟内趁记忆新鲜记下问了什么/答得怎样，写进七节进度日志 |

---

## 六、过往面试复盘（cherry-pick，只挑对MainCode有迁移价值的）

来源：`companies/SONIQ/first_interview.md`(+`interviewer_questions.md`)、`companies/TownSquare/interview.md`，都是真实面试转写稿。**这里不是全量复盘，是只捞出跟MainCode这个"Python后端+模型serving+reliability"定位重合的部分**。TownSquare那场考的React内部机制(useMemo/reconciliation)、GraphQL N+1、RAG/hallucination——跟MainCode不搭边（JD明确排除前端/prompt engineering），留到以后准备偏前端/RAG岗位时再系统复盘，现在不占用时间。

**1. JWT签名算法知识缺口（SONIQ技术轮，reset password深挖）**：被问"what encoding algorithm did you use"时回答是"I think it's RSA, but I'm not sure, I didn't go deeper in that algorithm"。这个缺口便宜且直接对应MainCode JD的"authentication and access controls"，已经排进上面周三Block 1.5去补。

**2. SONIQ技术轮里其实答得不错、可以直接迁移的部分**：reset password的过期时间设计(30分钟，复用了注册验证邮件的1小时链接模式)、用数据库字段持久化追踪"链接是否已被点击/使用"、测试方法(先mock数据单测，再连真实数据库跑一遍)、部署交给team lead但自己要写单元测试+GitHub Actions跑CI——这套"设计一个有时效性、需要持久化状态追踪、需要测试覆盖的认证类feature"的完整思路，直接可以迁移成MainCode面试里"如何设计一个可靠的系统"的素材，比临时现编更真实。

**3. 一个跨SONIQ和TownSquare两场反复出现的模式，比任何单个知识点都重要**：TownSquare被问`useMemo`/memoization和"状态管理用Context还是状态管理库"时，明显是"平时确实这么用，但被问到定义/原理时讲不清楚、边说边犹豫"的状态（原话："What reason you come to my mind now?"、"I'm not sure. Does next this has other?"）。但事实上你在MBSPro的`useSuggestResults.ts`里深度设计过Zustand的`persist`+`partialize`逻辑，还debug过一次真实的Zustand session bug（BeeQuant的`auth-provider.tsx`）——**这不是不会，是"日常顺手用的东西，被临时问概念定义时说不利索"**。MainCode的技术轮/take-home之后还有面向全team的presentation，这个模式如果不特意练，大概率还会重演。**练法不是去啃React官方文档背定义，是挑一两个你实际用过的概念(比如Zustand persist、rate limiting、retry/backoff)，对着空气大声、简洁地解释一遍"是什么/为什么这么设计/替代方案是什么"，practice出声表达，而不是只在脑子里过一遍。**

---

## 七、进度日志

*(每次准备/推进后在这里追加一条，记录做了什么、学到了什么、还差什么——方便下一次面试前快速回忆，不用重新问一遍)*

- 2026-07-14 建档：完成JD拆解、Glassdoor流程梳理、差距评估、周二/周三学习计划制定。尚未开始执行。
- 2026-07-14 补充：更正Amana Living状态(二面已完成，MainCode现在是唯一重心)；cherry-pick复盘了SONIQ/TownSquare两场旧面试转写稿，只挑出对MainCode有迁移价值的部分加进六节(JWT签名算法缺口、认证类feature设计的可迁移素材、"实际会用但说不清概念"的跨面试反复模式)，其余React/GraphQL/RAG相关内容判断为低优先级，留到以后系统复盘。
- 2026-07-14 补充：拿到确切面试信息(2026-07-16周四9:00-9:15am AWST，面试官Kim，Google Meet)，重排了周四时间表(7:30提前起床)。之后逐块核对了表格和说明文字，修掉三处不一致：①结构化日志原本没被排进任何时间段，现在并入周二Block 2b；②周三Block 1原本表格写"认证+日志"但文字只写认证+Docker，现在两边统一成"认证+可选Docker"；③周三练习引用的"周二写好的话术"原本从没被真正写出来过，现在新增4.1节cheat sheet草稿(含实际英文台词)，并在周二加了Block 4专门起草/定稿它。
- 2026-07-14 补充：用户自行调研了Maincode官网/研究博客/AMD案例/Forbes/ACS等来源，比Glassdoor扎实得多，整理进新增的2.1节。关键收获：①确认了这是真·从零训练模型+自建GPU集群+自研Rust推理引擎的公司，不是套壳AI应用；②Maincoder-1B(已发布/有公开benchmark)比Matilda(参数/benchmark未公开)更适合当具体例子引用，Matilda的具体数字不要在面试里瞎猜；③重要澄清：这个backend岗位大概率是坐在自研Rust推理引擎"上层"的Python服务，不是GPU kernel层，gap比想象中小一些；④用真实信息填好了4.1节"why Maincode"和反问问题(不再是占位)，周二Block 1(查公司资料)已完成可以跳过，Block 3(概念阅读)改成精读Maincode自己那篇Rust推理引擎博客，比读泛泛的vLLM资料更有针对性和诚意。
- 2026-07-14 新增独立项目 **Aurora**（`companies/MainCode/Aurora/`，独立git仓库，不嵌在这个私人求职资料仓库里）：一个生产级LLM serving平台的portfolio项目，目标是"如果Maincode给一个take-home，交一份能证明这个人能写Matilda生产系统的作品"，而不是一个demo。技术选型最终定为**Python 3.12 + FastAPI + Celery(Redis broker) + PostgreSQL + Redis + Ollama(本地/Mac原生跑，未走Docker因为Metal不能透传给容器) + 未来vLLM(生产/GPU)**，通过统一的`ModelProvider`接口解耦——这是刻意放弃了作者更熟练的NestJS/TypeScript，理由是①这更贴近JD信号和真实LLM serving生态(vLLM/TGI/Ray Serve都是Python) ②直接补上三节差距评估里点名的gap#1"没有用Python做过上线的生产服务"，比用熟悉语言再做一个项目价值高得多。架构是四层(API/Application/Inference/Model)，API进程和Celery worker进程分离(不是retrofit，从Phase 1就是这样)，跨进程流式靠Redis pub/sub桥接，处理了Celery两个容易被忽略的坑(`task_acks_late`默认关闭会丢任务、取消不能用`revoke(terminate=True)`会误杀整个worker进程)。已完成Phase 0设计文档全套(`docs/PRD.md`/`ARCHITECTURE.md`/`DATA_MODEL.md`/`API_SPEC.md`/`SEQUENCE_DIAGRAMS.md`/`ERROR_HANDLING.md`/`DEPLOYMENT.md`/`ROADMAP.md`)+仓库骨架，代码还没开始写(按ROADMAP.md是刻意的，设计先行)。**这个项目现在是准备过程里比15分钟screen话术更值得投入的部分**——面试后续的technical/take-home环节会用到这里建立的词汇量和实操经验。
- 2026-07-14 周二下午2:27 **Aurora Phase 1 完整写完，并且接了真实 Docker Compose + Postgres + Redis + Ollama 端到端跑通验证**（不只是针对 MockProvider/SQLite/fakeredis 的测试替身）。过程中抓到 4 个只有真实基础设施才会暴露的 bug（Docker 构建缺 README、Celery 任务从没被注册过、Ollama usage 字段解析会崩、worker 里一个跨事件循环的 Postgres/Redis 连接 bug），完整的"问题→原因→怎么解决"记录在 Aurora 仓库的 `docs/DEVLOG.md`。之后又搭了一套**学习脚手架**：`docs/study/PHASE1_STUDY_GUIDE.md`（按一~八节顺序梳理的中文学习路径）、代码里带编号的 `【卷 X.X】` 中文导读注释（配了 VS Code Highlight 插件高亮成紫色）、以及 `docs/study/zh/` 下 7 份核心设计文档的完整中文翻译。**这直接让原计划周二/周三"搭一个玩具 FastAPI 项目"的环节变得多余**——Aurora 本身就是生产级实现，比玩具项目分量重得多。据此重排了本文档五节周二下午/周三的时间表：省下来的时间全部改成"按学习指南顺序吃透 Aurora 代码"，JWT/cheat sheet练习/logistics确认这几块保留但压缩、并且尽量嫁接到 Aurora 的真实代码上举例（比如 JWT 直接讲 `app/core/auth.py`）。**明确决定 Phase 2/3/4 这次周四之前不写、不学**——周四只是低风险的15分钟 motivation screen，真正会深挖技术的 take-home 环节还没排期，到时候投入 Phase 2/3/4 时效性更强；比起多学几个没验证过的 phase，把已经完整实现+真实验证过的 Phase 1 讲透，这两天投入产出比更高。
