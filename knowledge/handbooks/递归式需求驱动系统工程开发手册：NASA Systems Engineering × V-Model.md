# 递归式需求驱动系统工程开发手册
## NASA Systems Engineering × V-Model

---

# 1. 本手册解决什么问题

本手册用于指导复杂工程从现实问题一路推导到可实施的底层设计，并最终逐层证明整个系统：

1. **确实按照规格被正确实现；**
2. **最终确实解决了最开始的问题。**

核心过程为：

```text
P0 Problem
为什么需要这个系统？
        ↓
P1 Stakeholder & Operational Context
谁在什么环境中怎样使用它？
        ↓
P2 Requirements
当前系统必须做到什么？
        ↓
P3 Logical Decomposition
为了做到这些，逻辑上必须发生什么？
        ↓
P4 Architecture
由哪些下一级系统元素承担这些责任？
        ↓
        ┌─────────────────────────────┐
        │ 下一级元素仍然复杂？         │
        │                             │
        │ YES → 把它作为新的 SoI      │
        │       再运行 P0-P4          │
        │                             │
        │ NO  → 进入 P5               │
        └─────────────────────────────┘
        ↓
P5 Detailed Design
叶子元素具体怎样工作？
        ↓
P6 Implementation
如何制造、建设、配置或实现？
        ↓
P7 Verification
是否有证据证明规格满足？
        ↓
P8 Validation
是否真正完成上一级赋予的使命？
```

这里的 SoI 指：

# System-of-Interest

即：

> **当前正在被工程化分析的那个对象。**

它可以是：

- 整条城市轨道交通线路；
- 牵引供电系统；
- 一个车站；
- 一套制动系统；
- 一个转向架；
- 一个门控机构。

不同尺度使用的是**同一套工程问题**。

---

# 2. 最重要的原则：本手册是递归使用的

这套流程不是：

```text
整个项目跑一次 P0-P8
↓
然后直接一路拆到零件
```

而是：

```text
当前 SoI
↓
P0
P1
P2
P3
P4
↓
得到下一级系统元素
```

然后逐个判断：

> **这个元素是不是已经简单到可以直接详细设计？**

如果答案是否定的：

```text
把它提升成新的 SoI
↓
重新执行 P0 → P4
```

因此整个工程过程具有一种**工程意义上的分形/递归同构结构**。

这里的“分形”不是数学意义上的严格 fractal。

它指的是：

> **无论观察尺度如何变化，工程推理所使用的问题结构保持基本一致。**

---

# 3. 贯穿示例：Metro-X 城市轨道交通线路

后续所有正例和反例统一围绕一个大型非软件系统：

# Metro-X 城市轨道交通线路

目标是在一条人口密集的城市交通走廊上建设新的轨道交通线路。

整个系统可能包含：

```text
Metro-X
│
├── 线路与土建系统
│   ├── 隧道
│   ├── 高架
│   └──轨道
│
├── 车辆系统
│
├── 车站系统
│
├── 信号与列车控制系统
│
├── 牵引供电系统
│
├── 通信系统
│
├── 通风与环境控制系统
│
├── 屏蔽门及乘客设施
│
├── 车辆基地与维修系统
│
├── 运营组织体系
│
└── 应急与安全体系
```

这个例子有几个好处：

- 既有物理设备，也有人员和运营流程；
- 存在明显的 system / subsystem / component 层级；
- 有性能、安全、容量、可靠性、成本等不同需求；
- 有大量 subsystem interface；
- 可以很好展示 Requirements → Logical Function → Architecture 的区别；
- 不会让人误以为 Architecture 就是“软件模块”。

---

# 4. Recursive 和 Iterative 必须区分

## Recursive：改变系统层级

例如：

```text
Metro-X
↓
牵引供电系统
↓
牵引变电系统
↓
整流机组
↓
功率模块
```

每进入下一层，都可以重新运行：

```text
P0 → P1 → P2 → P3 → P4
```

---

## Iterative：同一层重新分析

例如：

```text
牵引供电系统 P4
↓
发现当前架构无法满足单点故障要求
↓
返回同一层 P2 / P3
↓
修正需求理解或逻辑方案
↓
重新 P4
```

因此：

```text
Recursive = 换尺度

Iterative = 同尺度返工
```

大型工程通常同时存在两者。

---

# P0 — Problem Definition

## 本阶段唯一核心问题

> **当前 SoI 为什么需要存在？**

P0 只讨论现实问题和使命，不讨论解决方案。

---

## P0-Q1：谁在什么情况下遇到了什么核心问题？

### 回答模板

```text
[主体] 在 [现实场景] 下需要 [目标]，
但由于 [当前条件/机制]，
目前无法可靠地 [实现目标]。
```

### 正例

```text
城市东部居住区与中心商务区之间存在持续增长的大规模通勤需求，

现有道路和公交系统在早晚高峰已经接近或超过承载能力，

大量乘客无法在可接受的时间和稳定性水平内完成跨区域通勤。
```

### 反例

```text
城市需要建设一条 25 km 的地铁线路，
使用 A 型车和 CBTC 信号系统。
```

### 错误原因

已经跳到解决方案。

---

## P0-Q2：这个问题造成什么实质后果？

### 回答模板

```text
该问题造成：

- [后果 1]
- [后果 2]
- [...]

主要影响：
[时间 / 成本 / 安全 / 环境 / 经济 / 服务能力等]
```

### 正例

```text
该交通走廊高峰期平均通勤时间持续增加；

道路拥堵使公交运行时间波动显著；

道路继续扩容受到土地条件限制；

交通需求增长进一步增加地面交通拥堵和能源消耗。
```

### 反例

```text
当前交通状况很差，
市民出行体验不好。
```

### 重要要求

不能只使用：

```text
差
严重
不方便
不先进
```

必须指出可分析的实际后果。

---

## P0-Q3：为什么现有方式无法充分解决？

### 回答模板

```text
当前通过 [现有方式] 处理，

但当 [现实条件] 时，

由于 [具体限制机制]，

无法继续满足 [目标]。
```

### 正例

```text
当前主要依赖公交线路调整和道路交通组织优化，

但核心路段道路空间已经高度饱和，

进一步增加公交车辆反而会受到道路拥堵本身限制，

因此现有地面交通方式无法提供所需的稳定高峰运输能力。
```

### 反例

```text
公交技术已经比较落后，
所以需要修地铁。
```

### 重要要求

必须明确：

```text
现有手段是什么
+
在哪些条件下失效
+
为什么失效
```

---

## P0-Q4：什么现实结果意味着问题得到解决？

### 回答模板

```text
若问题得到解决，

[目标主体] 应能够 [获得某种现实能力或结果]，

同时显著减少 [原有核心问题]。
```

### 正例

```text
主要交通走廊上的乘客应能够获得
高容量、稳定且可预测的公共交通服务，

使高峰期跨区域通勤不再主要受地面道路拥堵影响。
```

### 反例

```text
建设 18 个车站、配置 30 列列车。
```

### 错误原因

这是方案规模，不是现实结果。

---

## P0-Q5：本轮工程明确解决什么，不解决什么？

### 回答模板

```text
In Scope:
- [...]

Out of Scope:
- [...]
```

### 正例

```text
In Scope:
- 主走廊公共交通运输能力
- 沿线乘客快速集散
- 线路日常运营与应急保障

Out of Scope:
- 全市所有交通拥堵问题
- 城际长距离铁路运输
- 私家车出行需求本身
```

---

# P1 — Stakeholder & Operational Context

## 本阶段唯一核心问题

> **当前 SoI 位于什么现实环境中，谁怎样与它交互？**

P0 是：

> 为什么存在？

P1 是：

> 存在于什么世界里？

---

## P1-Q1：哪些 Stakeholder 对当前 SoI 有真实关切？

### 回答模板

```text
Stakeholder:
[角色]

Concern:
- [...]
- [...]
```

### 正例

```text
Passengers:
- 通勤时间
- 乘车安全
- 服务可靠性
- 换乘便利性

Metro Operator:
- 正点率
- 运能
- 维护成本
- 故障恢复能力

City Government:
- 公共交通承载能力
- 建设成本
- 城市发展目标

Emergency Services:
- 事故条件下的进入、疏散和救援能力
```

### 反例

```text
Stakeholders:
列车
车站
供电系统
信号系统
```

### 错误原因

这些是系统元素，不是 Stakeholder。

---

## P1-Q2：当前 SoI 的边界以及外部实体是什么？

### 回答模板

```text
System-of-Interest:
[...]

Inside:
[...]

Outside:
[...]

External Interactions:
[...]
```

### 正例

```text
System-of-Interest:
Metro-X 线路系统

Inside:
车站、线路、车辆、供电、信号、
通信、维护和运营相关系统。

Outside:
城市道路系统
其他轨道线路
城市电网
消防与急救体系
市政管线
乘客
```

### 反例

```text
Metro-X 包括地铁相关的所有设施。
```

### 重要要求

边界不清晰会直接导致后续 Requirement 和 Interface 混乱。

---

## P1-Q3：正常运行场景是什么？

### 回答模板

```text
Given:
[运行条件]

Actor:
[外部主体行为]

System Behavior:
[黑箱层面的系统行为]

Outcome:
[现实结果]
```

### 正例

```text
Given:
工作日早高峰交通需求。

Actor:
大量乘客从东部住宅区进入车站并前往中心城区。

System Behavior:
Metro-X 按高峰运行图连续运输乘客，
完成进站、候车、乘车、换乘和出站过程。

Outcome:
乘客在可预测时间内完成跨区域通勤。
```

### 反例

```text
ATO 控制列车起步，
CBTC 根据移动闭塞计算运行许可。
```

### 错误原因

已经进入内部工作机制。

---

## P1-Q4：关键异常运行场景是什么？

### 回答模板

```text
When:
[现实异常]

Expected External Behavior:
[系统整体对外应表现为什么]
```

### 正例

```text
When:
高峰运行期间一列车发生无法继续运营的故障。

Expected External Behavior:
系统应控制故障影响范围，
避免造成后续列车连续失序，
并在可接受时间内恢复线路运输能力。
```

### 反例

```text
故障列车由 OCC 下发扣车命令，
然后执行反向运行。
```

### 重要要求

P1 只定义 Operational Outcome。

内部故障处置方案属于后续阶段。

---

## P1-Q5：当前 SoI 必须适应什么运行环境？

### 回答模板

```text
Operational Environment:

Physical Environment:
[...]

Demand Environment:
[...]

External Dependencies:
[...]

Expected Disturbances:
[...]

Lifecycle Conditions:
[...]
```

### 正例

```text
线路在地下和高架混合环境运行；

工作日早晚高峰客流高度集中；

外部依赖城市电网供电；

可能出现暴雨、高温、电网扰动、
设备故障和大型活动客流；

系统设计寿命跨度达到数十年。
```

---

# P2 — Requirements

## 本阶段唯一核心问题

> **当前 SoI 必须做到什么，以及做到什么程度？**

这是正式 Specification。

---

## P2-Q1：当前 SoI 必须提供什么功能能力？

### 回答模板

```text
ID:
REQ-F-xxx

Statement:
The [SoI] shall [observable capability].

Parent:
[P0/P1 来源]

Rationale:
[为什么需要]
```

### 正例

```text
REQ-F-021

Metro-X shall transport passengers
between all revenue-service stations
during scheduled operating periods.
```

### 反例

```text
Metro-X shall use 6-car Type-A trains
to transport passengers.
```

### 错误原因

把实现方案写进了功能需求。

---

## P2-Q2：这些能力必须达到什么性能或质量水平？

### 回答模板

```text
ID:
REQ-Q-xxx

Condition:
[...]

Metric:
[...]

Required Level:
[...]
```

### 正例

```text
REQ-Q-014

Condition:
设计高峰小时。

Metric:
Directional passenger carrying capacity.

Required Level:
线路单方向小时运输能力不得低于
规定的设计客流需求及容量裕度。
```

### 反例

```text
Metro-X 应具有很大的运输能力。
```

### 重要要求

常见质量维度包括：

```text
容量
时延
安全
可靠性
可用性
维护性
舒适度
寿命
环境适应性
```

---

## P2-Q3：当前 SoI 必须满足哪些外部接口要求？

### 回答模板

```text
External Entity:
[...]

Required Interaction:
[...]

Observable Contract:
[...]
```

### 正例

```text
External Entity:
城市电网

Required Interaction:
Metro-X 从规定电网接入点获得运营所需电能。

Observable Contract:
正常运营和规定故障场景下，
供电接口必须满足线路运行所要求的电压、
容量和保护协调条件。
```

### 反例

```text
主变电站通过 35 kV 电缆接入两路电源。
```

### 错误原因

这是具体架构方案。

---

## P2-Q4：有哪些不可违反的外部约束？

### 回答模板

```text
Constraint:
[...]

Source:
[法规 / 地理 / 城市规划 / 既有系统等]
```

### 正例

```text
线路必须与既有 Metro-A 线路
在 Central Station 实现乘客换乘。

Source:
批准的城市轨道交通线网规划。
```

### 反例

```text
最好采用岛式站台。
```

---

## P2-Q5：如何判定每条 Requirement 被满足？

### 回答模板

```text
Acceptance Criterion:

Given:
[...]

When:
[...]

Then:
[...]

Verification Method:
Test / Analysis / Inspection / Demonstration
```

### 正例

```text
Given:
设计高峰小时需求。

When:
按照批准的列车运行图计算线路运输能力。

Then:
单方向小时运输能力必须达到 REQ-Q-014。

Verification Method:
Analysis + Operational Demonstration
```

### 反例

```text
确认线路运能足够。
```

---

# P3 — Logical Decomposition

## 本阶段唯一核心问题

> **先不决定由什么设备、组织或子系统实现，为满足 Requirement，逻辑上必须发生什么？**

这一层特别重要。

因为：

```text
Requirement
```

不能直接跳成：

```text
设备清单
```

---

## P3-Q1：逻辑上必须执行哪些功能？

### 回答模板

```text
LF-xxx:
[Verb] [Object]

Satisfies:
REQ-xxx
```

### 正例

```text
Acquire Passenger Demand
Admit Passengers
Move Passengers Along Route
Control Vehicle Separation
Supply Traction Energy
Exchange Passengers at Stations
Manage Disturbed Operation
Evacuate Passengers
Restore Normal Service
```

### 反例

```text
车辆系统
信号系统
供电系统
车站系统
```

### 错误原因

这些是物理/架构元素，而不是逻辑行为。

---

## P3-Q2：逻辑功能之间有什么时序、条件和依赖？

### 回答模板

```text
Function A
      ↓ [condition]
Function B
      ↓
Function C
```

### 正例

```text
Admit Passenger
      ↓
Validate Travel Authorization
      ↓ valid
Provide Access to Platform
      ↓ train available
Board Passenger
      ↓
Transport Passenger
      ↓ destination
Alight Passenger
      ↓
Release Passenger from System
```

### 反例

```text
闸机连接 AFC 系统，
AFC 再连接车站服务器。
```

### 错误原因

这是结构和设备连接关系。

---

## P3-Q3：系统有哪些逻辑状态或运行模式？

### 回答模板

```text
STATE-A
 -- event [guard] -->
STATE-B
```

### 正例

```text
NORMAL_OPERATION
    ↓ major train failure
DEGRADED_OPERATION
    ↓ recovery completed
NORMAL_OPERATION

NORMAL_OPERATION
    ↓ evacuation condition
EMERGENCY_OPERATION
```

### 反例

```text
控制器寄存器值 0 表示正常，
1 表示故障。
```

---

## P3-Q4：什么逻辑信息、物料或能量必须流动？

### 回答模板

```text
Flow:
[...]

Source:
[...]

Destination:
[...]

Required Meaning / Property:
[...]
```

### 正例

```text
Flow:
Traction Energy

Source:
External Electrical Supply

Destination:
Moving Train

Required Property:
必须在车辆加速和制动工况下
满足规定功率和电能质量要求。
```

也可以是信息流：

```text
Flow:
Train Position Information

Producer:
Determine Train Position

Consumer:
Control Train Separation
```

### 反例

```text
使用以太网传输列车位置数据。
```

### 错误原因

以太网已经是实现方式。

---

## P3-Q5：发生失败或异常时，逻辑上必须怎样响应？

### 回答模板

```text
Failure:
[...]

Required Logical Response:

Detect
→ Classify
→ Contain
→ Maintain / Recover
→ Result
```

### 正例

```text
Failure:
单列车失去牵引能力。

Required Logical Response:

Detect abnormal train state
→ Determine affected operating area
→ Prevent unsafe train movement
→ Maintain remaining service where possible
→ Remove or isolate failed train
→ Restore scheduled operation
```

### 反例

```text
由后车推进故障列车到折返线。
```

### 错误原因

这是具体恢复方案。

---

## P3-Q6：逻辑分解产生了哪些新的派生 Requirement？

### 回答模板

```text
ID:
DER-xxx

Derived From:
[...]

Because:
[...]

Statement:
[...]
```

### 正例

```text
DER-031

Derived From:
Control Train Separation

Because:
安全控制列车间隔必须知道
每列运行列车的位置和运动状态。

Statement:
The system shall provide sufficiently accurate
and timely train-position information
for safe separation control.
```

### 反例

```text
因此需要在轨旁安装应答器。
```

### 错误原因

应答器是一种候选 Design Solution。

---

# P4 — Architecture Design

## 本阶段唯一核心问题

> **由哪些真实系统元素承担 P3 中定义的逻辑责任？**

这是：

```text
Logical Structure
↓
Physical / Organizational Structure
```

的转换点。

---

## P4-Q1：需要哪些下一级系统元素？

### 回答模板

```text
Element:
[...]

Exists To:
[承担的职责]
```

### 正例

```text
Rolling Stock System
→ 承担乘客移动和车载运行能力

Train Control System
→ 承担安全列车间隔和运行控制

Traction Power System
→ 承担列车牵引能源供应

Station System
→ 承担乘客集散、候车和上下车环境

Operations Control
→ 承担线路运营协调和异常运行组织
```

### 反例

```text
建立一些车辆、信号和供电相关系统。
```

---

## P4-Q2：每项逻辑责任由谁主要承担？

### 回答模板

```text
Logical Function:
LF-xxx

Primary Owner:
[System Element]

Supporting Elements:
[...]
```

### 正例

```text
LF-Control-Train-Separation

Primary Owner:
Train Control System

Supporting:
Rolling Stock
Operations Control
Communication System
```

### 反例

```text
列车安全由车辆、信号和司机共同负责。
```

### 错误原因

责任边界不清，无法确定谁对 capability 负责。

---

## P4-Q3：每个元素负责什么，不负责什么？

### 回答模板

```text
Element:
[...]

Owns:
- [...]

Does Not Own:
- [...]
```

### 正例

```text
Train Control System

Owns:
- 安全列车间隔控制
- 运行许可生成
- 与列车运动安全相关的控制逻辑

Does Not Own:
- 车辆自身机械制动能力
- 牵引电力供应
- 乘客疏散组织
```

### 反例

```text
信号系统主要负责列车运行相关事务。
```

---

## P4-Q4：关键状态、资源或资产由谁权威拥有？

### 回答模板

```text
Resource / State:
[...]

Authoritative Owner:
[...]

Others:
[Use / Request / Observe / Maintain]
```

### 正例

```text
Train Physical Movement State

Authoritative Physical Owner:
Rolling Stock

Train Control System:
获取并使用该状态进行安全控制，

但不能改变车辆真实动力学状态本身，
只能通过控制指令影响车辆行为。
```

### 反例

```text
车辆状态由多个系统共同维护。
```

---

## P4-Q5：系统元素之间通过什么接口交互？

### 回答模板

```text
Element A
→ [Exchange / Interface]
→ Element B

Transferred:
[...]

Required Properties:
[...]
```

### 正例

```text
Train Control System
→ Train Control Interface
→ Rolling Stock

Transferred:
movement authority / speed constraints / train status

Required Properties:
timeliness
integrity
fail-safe behavior
```

### 反例

```text
信号系统和车辆系统需要联网。
```

---

## P4-Q6：系统级流程由谁协调？

### 回答模板

```text
Operational Process:
[...]

Coordinator:
[...]

Participants:
[...]
```

### 正例

```text
Major Service Disruption Recovery

Coordinator:
Operations Control

Participants:
Train Control
Rolling Stock
Station Operations
Maintenance
Emergency Response
```

### 反例

```text
出现故障时各专业按照各自规程处理。
```

### 错误原因

复杂跨系统流程没有明确系统级控制责任。

---

## P4-Q7：为什么选择这种架构，而不是其他方案？

### 回答模板

```text
Decision:
[...]

Drivers:
[...]

Alternatives:
A:
B:
C:

Chosen:
[...]

Reason:
[...]

Trade-offs:
[...]
```

### 正例

```text
Decision:
主走廊采用全封闭轨道交通，
而不是进一步建设快速公交。

Drivers:
- 高峰需求规模
- 道路空间约束
- 运行稳定性要求
- 长期容量增长

Alternative A:
快速公交系统

Alternative B:
全封闭城市轨道交通

Chosen:
B

Trade-offs:
建设成本和周期显著提高，
但获得更高容量、独立路权和运行稳定性。
```

### 反例

```text
采用地铁方案，
因为大城市一般都使用地铁。
```

---

# 5. P4 是递归分叉点，而不是“架构设计结束”

假设 Metro-X 顶层 P4 得到：

```text
Metro-X
│
├── Rolling Stock System
├── Train Control System
├── Traction Power System
├── Station System
├── Communication System
└── Operations System
```

这并不意味着接下来就直接设计零件。

例如：

# Traction Power System

仍然明显具有复杂性。

所以把它声明为新的：

```text
System-of-Interest
```

重新运行整个前半段。

---

# 6. 第二层示例：Traction Power System

## L1-P0：为什么它需要存在？

```text
列车运行需要持续获得足够的牵引能源，

而城市公共电网提供的电能形式、
保护方式和供电结构
不能直接等同于列车运行所需的牵引供电能力。

因此 Metro-X 需要一个独立的牵引供电系统，
把外部电能转化、分配并可靠提供给运行列车。
```

---

## L1-P1：它的运行环境是什么？

```text
Parent System:
Metro-X

External Entities:
城市电网
Rolling Stock System
Maintenance Organization
Operations Control

Operational Conditions:
高峰列车密集运行
再生制动
部分设备故障
外部电网扰动
检修停电
```

---

## L1-P2：它必须做到什么？

```text
TPS-REQ-01

Traction Power System shall supply
electrical energy required by scheduled train operation.

TPS-REQ-02

A single defined power-supply failure
shall not cause loss of traction power
over an unacceptable extent of the line.

TPS-REQ-03

The system shall permit electrical isolation
of defined maintenance sections.
```

---

## L1-P3：逻辑上必须发生什么？

```text
Receive External Power
↓
Transform Electrical Energy
↓
Convert Energy for Traction Use
↓
Distribute Traction Energy
↓
Deliver Energy to Train
↓
Detect Electrical Fault
↓
Isolate Fault
↓
Reconfigure Supply
```

---

## L1-P4：哪些元素承担这些责任？

```text
Traction Power System
│
├── Grid Connection System
├── Main Substation
├── Traction Substation
├── Traction Distribution Network
├── Return Circuit
├── Protection System
└── Power Supervisory System
```

现在：

```text
Traction Substation
```

仍然可能非常复杂。

于是再次成为新的 SoI。

---

# 7. 第三层示例：Traction Substation

重新运行：

```text
P0 为什么它必须存在？
P1 它与谁交互？
P2 它必须满足什么要求？
P3 内部逻辑上必须发生什么？
P4 哪些设备承担？
```

可能进一步得到：

```text
Traction Substation
│
├── Incoming Switchgear
├── Traction Transformer
├── Rectifier Unit
├── DC Switchgear
├── Protection Equipment
└── Local Control Equipment
```

如果：

```text
Rectifier Unit
```

仍然复杂，则继续递归。

最终可能细化到：

```text
Rectifier Unit
↓
Power Module
↓
Semiconductor Device Assembly
```

到某一层之后，工程师已经不再需要重新决定：

```text
这个系统到底负责什么？
应该分成哪些独立设备？
谁拥有哪项主要功能？
```

此时才进入 P5。

---

# 8. 不同层级下，P0-P8 的问题含义如何变化

| 阶段 | Metro-X 顶层 | Traction Power 子系统 | Rectifier Unit |
|---|---|---|---|
| P0 | 为什么需要这条线路？ | 为什么需要牵引供电？ | 为什么需要整流功能？ |
| P1 | 城市、乘客、外部交通环境 | 电网、列车、线路运行环境 | 输入电源、负载、热环境 |
| P2 | 线路必须做到什么？ | 供电系统必须做到什么？ | 整流单元必须做到什么？ |
| P3 | 运输功能如何逻辑完成？ | 电能如何逻辑转换和分配？ | AC→DC 逻辑过程是什么？ |
| P4 | 哪些子系统承担？ | 哪些供电设备承担？ | 哪些器件/模块承担？ |
| P5 | 通常不会直接进入 | 视复杂度决定 | 可以开始详细设计 |
| P6 | 整体建设 | 子系统制造/安装 | 器件实现 |
| P7 | 线路规格是否满足？ | 供电规格是否满足？ | 电气规格是否满足？ |
| P8 | 是否解决交通使命？ | 是否真正支撑线路运营？ | 是否真正履行供电子系统使命？ |

所以：

> **相同问题结构在不同尺度重新解释。**

这才是“分形”的真正含义。

---

# 9. 什么时候继续递归？

如果一个系统元素仍然存在以下情况之一，就应考虑继续 P0-P4：

### 1. 仍然包含多个相对独立责任

例如：

```text
Traction Power System
```

显然不只是一个动作。

---

### 2. 具有自己的生命周期

例如：

```text
列车车辆系统
```

有：

```text
运营
停放
检修
故障
救援
退役
```

---

### 3. 存在重要内部状态

例如：

```text
车站系统
```

存在：

```text
正常运营
大客流
部分封闭
火灾
疏散
停运
```

---

### 4. 存在多个复杂接口

例如牵引供电与：

```text
电网
车辆
信号
维修
消防
控制中心
```

都有接口。

---

### 5. 存在独立 Requirement

如果一个元素已经有几十条 Requirement，

通常不适合直接进入 Detailed Design。

---

### 6. 仍然需要决定“谁负责什么”

这是最强的判断标准。

如果工程师还在争论：

```text
这个功能到底应该属于哪个设备？
这个状态谁拥有？
这个故障由谁处理？
```

说明还处在 P3/P4，而不是 P5。

---

# 10. 什么时候停止递归？

不是拆到：

```text
零件
```

才停止。

也不是必须：

```text
一直拆到物理原子级
```

而是当当前元素已经满足：

```text
责任明确
边界明确
接口明确
输入输出明确
状态明确
不变量明确
性能要求明确
失败语义明确
```

并且：

> **设计者不再需要进行新的跨元素架构分配。**

即可停止递归。

这可以称为：

# Leaf Design Criterion

即：

> 当前元素已经成为可以直接进行 Detailed Design 的叶子。

---

# P5 — Detailed Design

## 本阶段唯一核心问题

> **这个叶子系统元素具体怎样工作？**

P4 回答：

> 谁负责？

P5 才回答：

> 具体怎样做到？

---

## P5-Q1：这个元素的外部接口精确定义是什么？

### 回答模板

```text
Interface:
[...]

Input:
[...]

Output:
[...]

Preconditions:
[...]

Postconditions:
[...]
```

### 正例

以一个牵引变电设备为例：

```text
Interface:
Traction DC Output Interface

Input:
规定范围内的 AC electrical supply

Output:
规定电压范围内的 DC traction supply

Preconditions:
上游供电有效且保护系统无闭锁状态

Postconditions:
输出满足规定电压、电流和保护条件
```

### 反例

```text
整流设备把交流电变成直流电。
```

### 错误原因

还不足以形成可实现 Contract。

---

## P5-Q2：核心参数、内部状态和不变量是什么？

### 回答模板

```text
Element:
[...]

Parameters:
[...]

States:
[...]

Invariant:
Always [...]
```

### 正例

```text
Rectifier Unit

States:
OFF
READY
ENERGIZED
FAULT_ISOLATED

Invariant:
在检测到规定内部短路故障后，
设备不得继续维持危险输出状态。
```

### 反例

```text
设备必须保持安全。
```

---

## P5-Q3：内部工作流程或物理机制是什么？

### 回答模板

```text
Input
↓
Step / Physical Transformation
↓
Decision / Control
↓
Step
↓
Output
```

### 正例

```text
AC Input
↓
Voltage Transformation
↓
Rectification
↓
Output Filtering / Stabilization
↓
Protection Monitoring
↓
DC Traction Output
```

### 反例

```text
设备内部按照电气原理工作。
```

---

## P5-Q4：异常和故障语义是什么？

### 回答模板

```text
Condition:
[...]

Required Local Response:
[...]

Reported State:
[...]

Parent-System Expectation:
[...]
```

### 正例

```text
Condition:
整流单元内部短路。

Required Local Response:
快速切断故障电流并进入隔离状态。

Reported State:
FAULT_ISOLATED

Parent-System Expectation:
牵引供电系统可以识别该单元不可用，
并决定是否重构供电。
```

### 反例

```text
故障时进行保护。
```

---

## P5-Q5：并行、冗余、一致性和资源共享规则是什么？

### 回答模板

```text
Shared Resource:
[...]

Normal Configuration:
[...]

Failure Configuration:
[...]

Coordination Rule:
[...]
```

### 正例

```text
两套牵引整流机组并联承担正常负荷；

单机故障退出后，
剩余机组是否继续承担负荷
必须满足规定热容量和保护边界；

不得因一台设备内部故障
导致另一健康机组同时被错误切除。
```

### 反例

```text
采用冗余设计提高可靠性。
```

---

## P5-Q6：生命周期和维护规则是什么？

### 回答模板

```text
Lifecycle:
[...]

Inspection:
[...]

Maintenance:
[...]

Replacement:
[...]

Return-to-Service Criteria:
[...]
```

### 正例

```text
设备进入维护隔离状态后，
必须满足明确的电气隔离条件；

维护完成后，
必须完成规定检查和功能测试
才能重新进入 READY 状态。
```

---

# P6 — Implementation

## 本阶段唯一核心问题

> **现实实现是否忠实地实现已经批准的设计？**

Implementation 不一定是“写代码”。

在不同系统里，它可能意味着：

```text
制造
采购
施工
装配
焊接
布线
安装
配置
编程
调试
```

---

## P6-Q1：实际产物对应哪个 Design Element？

### 回答模板

```text
Implementation Item:
[...]

Implements:
[...]

Applicable Specification:
[...]
```

### 正例

```text
Item:
TS-03 Rectifier Unit

Implements:
Traction Substation Detailed Design DD-TS-RECT-03

Applicable Requirements:
TPS-R-041
TPS-R-043
TPS-R-051
```

### 反例

```text
这是一套牵引整流设备。
```

---

## P6-Q2：实现是否满足 Contract 和 Invariant？

### 模板

```text
Design Rule:
[...]

Implementation Evidence:
[...]

Status:
PASS / FAIL
```

---

## P6-Q3：实施过程中有没有突破既定系统边界？

### 正例

```text
施工阶段发现牵引变电站
需要借用通信系统未定义的专用数据链路。

这不是现场施工问题，

而是新的 P4 Interface Architecture 问题，

必须返回相应层级重新设计。
```

### 反例

```text
现场先增加一根通信电缆，
后面再补图纸。
```

---

## P6-Q4：实施过程中是否出现新的设计决策？

### 回答模板

```text
Observed Issue:
[...]

Is New Design Decision Required?
YES / NO

Affected Level:
P3 / P4 / P5

Action:
[...]
```

### 重要要求

禁止通过现场 improvisation 隐藏架构缺陷。

---

# P7 — Verification

## 本阶段唯一核心问题

> **有什么客观证据证明当前 SoI 符合它已经定义的 Specification？**

这是：

# Did we build it right?

---

## P7-Q1：每条 Requirement 是否有验证证据？

### 回答模板

```text
Requirement:
[...]

Verification Method:
[...]

Acceptance Criterion:
[...]

Evidence:
[...]

Status:
PASS / FAIL / BLOCKED
```

### 正例

```text
Requirement:
TPS-REQ-02

Verification:
Failure-condition power-flow analysis
+
现场切换试验

Acceptance Criterion:
规定单点故障后，
剩余供电结构仍能满足指定运营模式。

Status:
PASS
```

### 反例

```text
牵引供电系统整体测试正常。
```

---

## P7-Q2：接口 Contract 是否得到验证？

### 正例

```text
Interface:
Traction Power ↔ Rolling Stock

Requirement:
列车最大牵引和再生制动条件下，
接口电压必须保持规定范围。

Verification:
车辆-供电联合动态仿真
+
线路联调试验。
```

### 反例

```text
车辆和供电各自测试都通过了，
所以接口应该没问题。
```

---

## P7-Q3：多个系统元素集成以后是否符合预期？

### 模板

```text
Integrated Elements:
[...]

Scenario:
[...]

Expected:
[...]

Observed:
[...]
```

### 正例

```text
Integrated Elements:
Rolling Stock
Train Control
Traction Power

Scenario:
高密度列车连续发车。

Expected:
列车按照计划间隔运行，
供电电压保持允许范围，
控制系统不因电压扰动产生异常制动。

Observed:
符合要求。
```

---

## P7-Q4：异常和边界情况是否得到验证？

### 正例

```text
Scenario:
单座牵引变电站退出。

Expected:
系统进入规定降级模式，
但不得扩大为整条线路失电。

Result:
PASS
```

### 反例

```text
正常运营测试成功，
极端故障出现概率很低，所以没有测试。
```

---

## P7-Q5：还有哪些 Verification Gap？

### 模板

```text
Requirement:
[...]

Status:
UNVERIFIED / BLOCKED

Reason:
[...]

Required Action:
[...]
```

### 重要原则

```text
Not Tested ≠ PASS
```

---

# P8 — Validation

## 本阶段唯一核心问题

> **当前 SoI 最终有没有完成它存在的使命？**

这是：

# Did we build the right thing?

---

# 11. Validation 在不同层级的含义不同

顶层 Metro-X：

> 它真的解决了城市交通走廊的运输问题吗？

牵引供电系统：

> 它真的为线路运营提供了所需的可靠牵引能源吗？

牵引变电站：

> 它真的在所在供电分区内完成了规定的电能转换和供电使命吗？

因此：

> **P8 不是每一级都去问最终乘客是否满意。**

而是：

> 当前 SoI 是否在它所属的父系统环境中真正完成了被赋予的使命？

---

## P8-Q1：当前 SoI 是否完成 Intended Mission？

### 回答模板

```text
System-of-Interest:
[...]

Intended Mission:
[...]

Operational Context:
[...]

Observed Outcome:
[...]

Conclusion:
[...]
```

### 正例

```text
System-of-Interest:
Traction Power System

Intended Mission:
为计划列车运营提供稳定牵引电能。

Operational Context:
设计高峰运行图及规定单点故障条件。

Observed Outcome:
列车能够维持规定运营能力，
没有出现不可接受的供电限制。

Conclusion:
Mission satisfied.
```

### 反例

```text
所有牵引变电站验收试验都通过了，
因此 Validation PASS。
```

### 错误原因

这是 Verification Evidence。

---

## P8-Q2：P0 定义的问题是否真正得到改善？

### 回答模板

```text
Original Problem:
[...]

Before:
[...]

After:
[...]

Evidence:
[...]
```

### 顶层正例

```text
Original Problem:
主交通走廊高峰运输能力不足且严重依赖拥堵道路。

Before:
公共交通旅行时间波动大，
高峰需求持续超过既有交通供给能力。

After:
Metro-X 提供独立路权的大容量运输能力，
高峰旅行时间稳定性显著提升。

Evidence:
实际运营客流、旅行时间和可靠性数据。
```

### 反例

```text
线路按期完成建设，
所有设备均通过验收。
```

### 为什么错误

系统可能完全按设计建成，

但仍可能没有真正解决交通需求。

---

## P8-Q3：真实运行环境下是否仍然成立？

### 正例

```text
Metro-X 在：

工作日高峰
大型活动客流
暴雨天气
列车故障
部分设备检修

等实际运行条件下，

仍能完成规定的运输使命。
```

### 反例

```text
空载试运行期间表现良好。
```

---

## P8-Q4：是否存在“所有规格都满足，但使命仍然失败”的情况？

### 正例

```text
Verification:
列车运行间隔、速度和运能均达到设计要求。

Validation:
实际乘客大量选择其他交通方式，

原因是车站位置和换乘路径
使真实门到门旅行时间没有改善。

Conclusion:
系统满足技术规格，
但顶层交通使命没有充分满足。

需要返回 P0/P1/P2
重新审视需求和 Operational Concept。
```

### 反例

```text
既然所有 Requirement 都 PASS，
系统一定成功。
```

---

# 12. 九个阶段之间的严格边界

| 阶段 | 唯一需要回答 | 本阶段不要回答 |
|---|---|---|
| **P0 Problem** | 为什么当前 SoI 需要存在？ | 如何解决 |
| **P1 Context** | 谁、在哪里、怎样使用/影响它？ | 内部怎么组成 |
| **P2 Requirements** | 当前 SoI 必须做到什么？ | 谁承担 |
| **P3 Logical** | 逻辑上必须发生什么？ | 用什么设备实现 |
| **P4 Architecture** | 哪些元素承担这些责任？ | 元素内部精确怎么实现 |
| **P5 Detailed Design** | 叶子元素内部怎样具体工作？ | 重新分系统责任 |
| **P6 Implementation** | 怎样忠实形成真实产品？ | 偷偷改变设计 |
| **P7 Verification** | 是否符合 Specification？ | 是否真正解决现实问题 |
| **P8 Validation** | 是否完成 Intended Mission？ | 某个局部参数是否合格 |

压缩为：

```text
P0  WHY

P1  CONTEXT

P2  WHAT

P3  LOGICAL WHAT

P4  WHO / WHAT OWNS WHAT

P5  EXACT HOW

P6  REALIZE

P7  PROVE SPECIFICATION

P8  PROVE PURPOSE
```

---

# 13. 整个工程不是一条线，而是一棵递归树

错误理解：

```text
需求
↓
系统
↓
子系统
↓
组件
↓
零件
```

这只描述了“结构越来越细”，

却没有说明：

> 每一次为什么这样拆。

正确模型：

```text
                         Metro-X
                      P0 P1 P2 P3 P4
                            │
          ┌─────────────────┼────────────────┐
          ▼                 ▼                ▼
     Rolling Stock      Traction Power    Stations
      P0...P4             P0...P4          P0...P4
                              │
                ┌─────────────┼────────────┐
                ▼             ▼            ▼
          Main Substation  Traction SS   Distribution
              P0...P4       P0...P4        P0...P4
                              │
                         Rectifier Unit
                           P0...P4
                              │
                            Leaf
                              ↓
                          P5 → P6
```

然后实现完成以后：

```text
Leaf Verification
        ↑
Component Integration
        ↑
Subsystem Verification / Validation
        ↑
System Integration
        ↑
Metro-X Verification
        ↑
Metro-X Validation
```

因此完整工程运动方向是：

```text
Top-down Definition

Mission
↓
Requirements
↓
Logical Decomposition
↓
Architecture
↓
Recursive Decomposition
↓
Leaf Design
↓
Implementation

Bottom-up Realization

Leaf Verification
↑
Integration
↑
Subsystem V&V
↑
Integration
↑
System V&V
↑
Mission Validation
```

---

# 14. 五条最高优先级工程纪律

## Rule 1 — No Leap

禁止：

```text
“需要高可靠供电”
↓
“建设两座变电站”
```

中间必须经过：

```text
Problem
↓
Requirement
↓
Logical Function
↓
Architecture Alternatives
↓
Trade-off
↓
Design Solution
```

---

## Rule 2 — No Orphan

任何重要：

```text
Requirement
Logical Function
Subsystem
Component
Interface
Verification Case
```

都必须能够回答：

> **我为什么存在？**

例如：

```text
Traction Substation
↑
Distribute Traction Energy
↑
Traction Energy Availability Requirement
↑
Metro-X Transport Capability
↑
City Mobility Need
```

---

## Rule 3 — Single Primary Ownership

一个关键责任必须有明确主要 Owner。

禁止：

```text
这个安全功能车辆负责一点，
信号负责一点，
运营也负责一点，
具体出了问题再协商。
```

支持系统可以很多，

但必须知道：

> 谁对 capability 的实现负责。

---

## Rule 4 — No Hidden Design

如果实施人员突然需要决定：

```text
这个功能究竟由谁负责？
是否要新增一个设备？
这个接口属于谁？
两个子系统谁控制谁？
故障时谁做最终决策？
```

说明问题不是 Implementation。

必须返回：

```text
P3 / P4 / P5
```

---

## Rule 5 — Evidence over Claim

禁止：

```text
应该安全
应该可靠
基本通过
大致满足
设备质量很好
```

必须形成：

```text
Need
↓
Requirement
↓
Logical Function
↓
Architecture Element
↓
Detailed Contract
↓
Implementation
↓
Verification Evidence
↓
Validation Evidence
```

---

# 15. 本手册的标准执行算法

面对任何复杂工程对象：

## STEP 1

明确：

```text
Current System-of-Interest = ?
```

没有 SoI，就不能开始工程分析。

---

## STEP 2 — P0

回答：

> 为什么它必须存在？

---

## STEP 3 — P1

回答：

> 它存在于什么环境，与谁发生关系？

---

## STEP 4 — P2

回答：

> 它必须做到什么？

---

## STEP 5 — P3

回答：

> 在不决定实现结构的前提下，逻辑上必须发生什么？

---

## STEP 6 — P4

回答：

> 哪些下一级系统元素承担这些逻辑责任？

---

## STEP 7 — 判断是否递归

对于 P4 得到的每一个重要元素：

```text
Does it still contain architectural uncertainty?
```

如果：

```text
YES
```

则：

```text
New SoI = this element

重新执行：
P0
P1
P2
P3
P4
```

如果：

```text
NO
```

则进入 P5。

---

## STEP 8 — P5

完成叶子级 Detailed Design。

---

## STEP 9 — P6

制造、建设、采购、实现或配置真实产品。

---

## STEP 10 — P7

从叶子开始逐层 Verification。

---

## STEP 11 — Integration

将叶子组合为父级系统元素。

---

## STEP 12 — Parent P7/P8

回答：

```text
父元素的 Requirement 是否满足？
父元素的 Intended Mission 是否满足？
```

---

## STEP 13

持续自底向上：

```text
Component
↑
Subsystem
↑
System
↑
System-of-Systems
```

直到最终回到顶层 P8：

> **最开始那个现实问题真的被解决了吗？**

---

# 16. 最终需要形成的 Traceability Chain

整个工程最理想的状态，是任何重要工程对象都能沿着链路上下追踪：

```text
Stakeholder Need
        ↓
System Requirement
        ↓
Logical Function
        ↓
Architecture Element
        ↓
Lower-Level Requirement
        ↓
Lower-Level Logical Function
        ↓
Lower-Level Architecture
        ↓
Detailed Design
        ↓
Implementation
        ↓
Verification Evidence
        ↓
Validation Evidence
```

例如：

```text
城市高峰走廊运输能力不足
        ↓
Metro-X 高峰运输能力 Requirement
        ↓
Move Passengers
        ↓
Rolling Stock + Train Control + Stations
        ↓
Rolling Stock Capacity Requirement
        ↓
Carry Passenger Load
        ↓
Vehicle Architecture
        ↓
Carbody / Door / Bogie / Propulsion ...
        ↓
Manufactured Train
        ↓
Vehicle Verification
        ↓
Line Integration
        ↓
Operational Validation
```

这样任何时候有人问：

> 为什么有这个设备？

能够向上追。

有人问：

> 怎么证明这个 Requirement 已经实现？

能够向下追。

---

# 17. 最终记忆模型

整本手册最终只需要牢牢记住九个问题：

```text
P0
为什么当前对象需要存在？

P1
它处于什么现实环境，与谁怎样交互？

P2
它必须做到什么？

P3
为了做到这些，逻辑上必须发生什么？

P4
哪些真实系统元素承担这些责任？

P5
叶子元素内部精确怎样工作？

P6
怎样忠实地形成真实产品？

P7
有什么证据证明规格已经满足？

P8
它最终真的完成自己的使命了吗？
```

以及一个递归规则：

> **如果 P4 得到的某个系统元素仍然复杂，就不要硬着头皮继续“详细设计”。把这个元素重新声明为 System-of-Interest，从 P0 开始运行同一套问题。**

因此真正的工程过程不是：

```text
一个 V
```

而是：

> **一棵由多个嵌套 V 组成的系统工程树。**

每个节点都经历：

```text
Purpose
→ Context
→ Requirement
→ Logical Decomposition
→ Architecture
```

每个叶子最终经历：

```text
Detailed Design
→ Implementation
```

然后整个树自底向上经历：

```text
Verification
→ Integration
→ Validation
```

这就是这套方法能够从一个巨大、模糊的现实问题，一直稳定细化到可制造、可建设、可实现的底层产品，同时又不会在逐层细化过程中失去工程依据的根本原因。