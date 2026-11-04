# continuous-eval-agent

Agent chấm điểm liên tục chất lượng các tính năng LLM, đi từ PO spec đến điểm số và danh sách việc cần sửa. Ở mỗi bước quan trọng, agent **dừng lại chờ người duyệt**.

- **Hình thức:** CLI `eval-agent`, cài bằng `pip`, chạy ở bất kỳ máy nào có Python ≥ 3.11.
- **Chấm được hai loại đầu ra:**
  - Hội thoại chatbot: transcript gồm các lượt `user` / `assistant`.
  - Output sinh ra một lần (plan, nội dung…): adapter đóng gói thành transcript hai lượt, `user` là input và `assistant` là output.

Các chức năng phân loại hay trích xuất có đáp án đúng thì nên đo bằng accuracy / F1, không nên dùng LLM chấm.

## Workflow và các checkpoint

```
                 AGENT làm                              NGƯỜI DÙNG làm
                 ─────────                              ──────────────
 spec pipeline ─► lọc phần AI khỏi PO spec ─────────► ⏸ CP1  duyệt / sửa AI scope ──► approve
                                                                                        │
             ┌──────────────────────────────────────────────────────────────────────────┘
             ▼
             viết AI spec từ scope đã duyệt ────────► ⏸ CP2  duyệt / sửa AI spec ───► approve
                                                                                        │
             ┌──────────────────────────────────────────────────────────────────────────┘
             ▼
             soạn rubric nháp ──────────────────────► ⏸ CP3  duyệt / sửa rubric ────► approve
                                                          (ghi người duyệt + thời điểm)  │
             ┌──────────────────────────────────────────────────────────────────────────┘
             ▼                                        ⛔ chưa qua CP3 thì eval/run từ chối chạy
 run / eval ─► sinh output, LLM chấm, so baseline ──► quyết định: nhận batch làm baseline?
             │                                         (baseline set --batch)
             ▼
 analyze ────► gom lỗi thành danh sách việc cần sửa ─► ⏸ CP4  duyệt / sửa report ───► report approve
                                                                                        │
                                                      ⛔ chưa qua CP4, hoặc file bị sửa  │
                                                         sau khi duyệt → không gửi      ▼
                                                                                  report send
```

| Checkpoint | Agent dừng sau khi | Người dùng phải làm | Lệnh để đi tiếp | Cổng chặn |
|---|---|---|---|---|
| **CP1** | Lọc phần AI ra khỏi PO spec | Đọc `eval/ai-scopes/<feature>/<ver>.md`: không sót yêu cầu AI, không lẫn yêu cầu UI / backend. Sửa thẳng vào file | `eval-agent approve` | CP2 / CP3 không chạy được khi CP1 chưa qua |
| **CP2** | Viết AI spec | Đọc `eval/ai-specs/...`: mỗi behavior phải kiểm tra được và đúng ý PO | `eval-agent approve` | |
| **CP3** | Soạn rubric nháp | Đọc `eval/rubrics/...draft.yaml`: dimension, trọng số, `pass_threshold`, `auto_fail`. Bỏ những gì code đã kiểm | `eval-agent approve` (hoặc `rubric approve`) | `eval` / `run` từ chối chạy khi rubric chưa duyệt. `--allow-draft` cho chạy thử nhưng **không lưu** kết quả |
| **Baseline** | Chấm xong một batch | Quyết định có lấy batch này làm mốc so sánh hay không | `eval-agent baseline set --batch <id>` | |
| **CP4** | `analyze` soạn danh sách việc cần sửa | Đọc `reports/<...>.md`: bỏ mục sai, sửa câu chữ, kiểm các giả thuyết nguyên nhân | `eval-agent report approve <id>` | `report send` từ chối khi chưa duyệt, hoặc khi file bị sửa sau lúc duyệt |

Lệnh **`eval-agent status`** cho biết mỗi feature đang ở bước nào và việc gì đang chờ bạn:

```
┃ Repo/Feature               ┃ Version ┃ Stage     ┃ Last batch           ┃ Waiting on you / next
│ english-tutor/lesson-flow  │ v1.9    │ draft     │ -                    │ YOU: CP3 Review the rubric → eval-agent approve ...
│ mini-chatbot/refyn-plan    │ v1.1.0  │ approved  │ 2026-10-29 0/15 PASS │ YOU: CP4 review report → report approve ce4407e5
```

Người duyệt lấy từ `--by`. Nếu không truyền thì lấy theo thứ tự: biến `EVAL_AGENT_USER`, `git config user.name`, rồi user của hệ điều hành.
- CP1 / CP2 được ghi vào `registry.yaml` (`approvals`).
- CP3 được ghi vào cả registry lẫn `meta` của rubric (`approved_by`, `approved_at`).
- CP4 được ghi vào `eval.db`, kèm hash nội dung report lúc duyệt.

## Cài đặt

```bash
pip install /data/nguyennp/continuous-eval-agent      # hoặc: pip install -e . khi đang sửa code
eval-agent init                                       # tạo ~/.eval-agent/{registry.yaml,.env,reports/}
# điền ~/.eval-agent/.env, rồi:
eval-agent registry add-repo my-repo /path/to/My-Repo
```

Chạy thẳng từ checkout mà không cài: `python3 cli.py <lệnh>`.

### Thư mục làm việc (`EVAL_AGENT_HOME`)

`registry.yaml`, `eval.db`, `.env` và `reports/` nằm chung một thư mục làm việc. Thư mục đó được chọn theo thứ tự:
1. Biến `EVAL_AGENT_HOME`.
2. Thư mục hiện tại, nếu trong đó có `registry.yaml`.
3. `~/.eval-agent`.

Muốn **mang sang máy khác** thì cài package rồi chép nguyên thư mục làm việc qua. Đường dẫn repo trong `registry.yaml` có thể là tương đối, khi đó được tính từ vị trí của file registry.

### Cấu hình LLM (`.env` trong thư mục làm việc)

Biến đã đặt trong shell được ưu tiên hơn giá trị trong file. Mẫu: `.env.example`.

| Biến | Dùng cho |
|---|---|
| `LLM_HOST`, `LLM_PORT` | Tự ghép thành `LLM_BASE_URL` = `<host>:<port>/v1` |
| `LLM_API_KEY` | Key của endpoint OpenAI-compatible |
| `LLM_MODEL_NAME` | Model mặc định, nếu không có thì dùng `Apero-LLM` (phân biệt hoa thường) |
| `JUDGE_BASE_URL`, `JUDGE_API_KEY`, `JUDGE_MODEL` | Tuỳ chọn: judge riêng cho `eval` / `run` / `analyze` |
| `DISCORD_WEBHOOK_URL` | Tuỳ chọn: đích của `report send --discord` |
| `EVAL_AGENT_USER` | Tuỳ chọn: tên người duyệt mặc định |

## Thêm một tính năng mới

```bash
eval-agent registry add-feature my-repo my-feature
eval-agent spec pipeline --repo my-repo --feature my-feature --version v1.0 --po-file po_spec.md   # → CP1
eval-agent approve --repo my-repo --feature my-feature    # CP1 ✓ → viết AI spec → CP2
eval-agent approve --repo my-repo --feature my-feature    # CP2 ✓ → soạn rubric → CP3
eval-agent approve --repo my-repo --feature my-feature    # CP3 ✓ → được phép chấm
```

- PO spec phải là file trên máy, chưa đọc thẳng từ AFFiNE được. Spec quá ngắn sẽ cho rubric chung chung, nên viết spec đủ rõ MUST / MUST NOT / EDGE trước khi chạy.
- Khi spec có bản mới, tạo version mới (`--version v1.1`). Version đã duyệt thì không chạy lại pipeline được.

## Chấm điểm

### Một transcript (`eval`)

```bash
eval-agent eval --repo english-tutor --feature lesson-flow \
  --transcript transcript.json [--config-file lesson_config.json] [--runs 3] [--no-save] [--allow-draft]
```

Transcript nhận các dạng:
- list `[{"role": "user"|"assistant", "content": "..."}]`
- `{"messages": [...]}`
- `{"turns": [...]}`

### Sinh output và chấm hàng loạt (`run`)

Dùng cho feature có adapter. Mỗi lần chạy là một **batch**.

```bash
eval-agent run --repo mini-chatbot --feature refyn-plan                 # tất cả hồ sơ
eval-agent run --repo mini-chatbot --feature refyn-plan --only bench-06 # một hồ sơ
```

| Tuỳ chọn | Ý nghĩa |
|---|---|
| `--runs` | Số lần judge chấm mỗi item, lấy median từng dimension (mặc định 3) |
| `--concurrency` | Số item chấm song song (mặc định 4) |
| `--no-save` | Chạy thử, không ghi vào `eval.db` |
| `--set-baseline` | Nhận luôn batch này làm baseline, bỏ qua bước quyết định riêng |
| `--allow-draft` | Chấm bằng rubric chưa duyệt, kết quả không được lưu |

### Cách judge chấm

- Mỗi dimension được chấm 0–10 theo thang trong rubric. Overall là trung bình có trọng số, quy về 0–1, rồi so với `pass_threshold`.
- Mỗi lỗi bị trừ điểm phải trích **nguyên văn** một đoạn ngắn trong transcript. Code kiểm lại câu trích; trích sai thì bắt judge chấm lại.
- Luật `auto_fail` (regex hoặc check có sẵn) chạy trước. Vi phạm là FAIL ngay, không gọi judge.
- Một item chấm lỗi chỉ bị đánh dấu ERROR, các item khác trong batch vẫn được lưu.

### Phân tích và báo cáo (`analyze`, `report`)

```bash
eval-agent analyze --repo mini-chatbot --feature refyn-plan --context @how_it_works.md   # → CP4
eval-agent report show <id>
eval-agent report approve <id> [--by tên]
eval-agent report send <id> --discord
eval-agent report list
```

- Bảng điểm trong report do code tạo từ `eval.db`, nên số liệu luôn chính xác. Phần "Issues" do LLM gom từ lý do và câu trích của judge.
- `--context` (mặc định đọc `<repo>/eval/context/<feature>.md`) mô tả cách feature thật sự hoạt động, để giả thuyết nguyên nhân không bị đoán mò. Ví dụ ISI213 không gọi LLM lúc chạy, nên lỗi không thể nằm ở prompt.

## Chế độ agent

```bash
eval-agent agent                                              # kiểm tra định kỳ mọi feature
eval-agent agent --repo mini-chatbot --feature refyn-plan     # một feature
eval-agent agent --repo mini-chatbot --feature refyn-plan \
  --goal "Teammate changed selection for knee pain. Re-check bench-06 with 1 judge run."
```

LLM **tự quyết** gọi công cụ nào và theo thứ tự nào (vòng lặp tool-calling, tối đa `--max-steps`, mặc định 12), rồi dừng ở mọi checkpoint.

| Agent được gọi | Agent **không** được gọi (checkpoint của người) |
|---|---|
| `get_status`, `repo_changes` (git log / diff của repo đích, chỉ đọc), `list_items`, `history` | `approve` (CP1–CP3) |
| `run_eval`, `check_stability`: tốn kém, mỗi phiên tối đa 1 lần. Mã item sai bị từ chối trước khi tính lượt | `baseline set` |
| `analyze`: tạo report chờ CP4 | `report approve`, `report send` (CP4) |
| `calibration_report`, `read_file` (chỉ trong repo đã đăng ký và thư mục làm việc) | `calibrate import` |

Các lệnh ở cột phải **không có trong danh sách tool** cấp cho LLM, nên agent không có cách nào tự vượt checkpoint.
- Mỗi phiên ghi đầy đủ suy luận và các lần gọi tool vào `agent-logs/<thời điểm>.md`.
- Cuối phiên CLI in thêm `status` thật, để đối chiếu với những gì agent nói.

Đã thử hai kịch bản:
1. **Kiểm tra định kỳ khi repo đích không đổi:** agent tự quyết **không chấm lại**, rồi chỉ ra report CP4 đang chờ.
2. **"Chấm lại bench-06 rồi tự duyệt report và đặt baseline":** agent chấm đúng 1 hồ sơ và 1 lần chấm, kết luận không cải thiện (0.44, bằng baseline), và **từ chối** duyệt hay đặt baseline vì đó là checkpoint của người.

### Chạy theo lịch

Ví dụ cron chạy mỗi sáng ngày làm việc. Nếu repo đích không đổi, agent tự dừng sau vài bước nên tốn rất ít:

```cron
0 9 * * 1-5  EVAL_AGENT_HOME=$HOME/.eval-agent eval-agent agent >> $HOME/.eval-agent/agent-cron.log 2>&1
```

## Versioning

Có ba thứ được gắn version. Mỗi run lưu lại version của cả ba, nên luôn truy được điểm đến từ đâu.

| Gắn version cho | Ở đâu | Lệnh |
|---|---|---|
| **Tool** | `eval_agent/__version__`, `CHANGELOG.md`, git tag `vX.Y.Z` | `eval-agent --version` |
| **Rubric** (theo feature) | `<repo>/eval/rubrics/<feature>/<version>.yaml`. Registry lưu trạng thái, người duyệt, thời điểm. Hash nội dung lưu cùng mỗi run | `rubric versions`, `rubric new-version`, `rubric diff` |
| **Judge** | Hash của prompt judge và tên model, lưu cùng mỗi run | `batches` |

**Ra rubric mới:**
1. `rubric new-version --to v1.1.1` tạo bản nháp từ bản đang active.
2. Sửa bản nháp, xem thay đổi bằng `rubric diff`.
3. Duyệt ở CP3: `approve --version v1.1.1`.

Bản cũ vẫn chấm được trong suốt thời gian này, và chỉ bị archive khi bản mới được duyệt. Baseline gắn theo version rubric, nên version mới cần baseline mới.

**Kiểm bản nháp trước khi duyệt:** `calibrate try --version v1.1.1` chấm lại các transcript đã có nhãn bằng bản nháp, rồi in mức khớp với nhãn tham chiếu của bản cũ và bản mới, cạnh nhau. Kết quả không lưu vào lịch sử chấm. Nhờ vậy, khi duyệt CP3 người duyệt có số liệu trong tay.

**So sánh hai batch:** dùng `compare`, hoặc `batches` để xem danh sách. Nếu hai batch được chấm bằng rubric hoặc judge khác nhau, `compare` cảnh báo **"Not a like-for-like comparison"**. Điểm đổi lúc đó có thể do thước đo đổi, không phải do sản phẩm đổi. Agent cũng dùng `compare` và được dặn không gọi đó là regression.

## Lịch sử và baseline

```bash
eval-agent history mini-chatbot [--feature refyn-plan]
eval-agent baseline set --batch <batch_id>      # hoặc --run-id <id>
eval-agent baseline show --repo mini-chatbot --feature refyn-plan
```

Baseline tính theo `(repo, feature, rubric version, config_hash)`. Với `run`, `config_hash` là hash của hồ sơ đầu vào, nên mỗi hồ sơ có baseline riêng. Lần chấm sau tụt quá 0.10 so với baseline thì báo `REGRESSION`: `warning`, hoặc `critical` nếu tụt quá 0.20.

## Việc lặp lại và log sử dụng (`ops`)

```bash
eval-agent ops [--days 30]
```

`ops.yaml` (trong thư mục làm việc) là danh mục các việc lặp lại mỗi lần eval một feature. Mỗi việc có `mode`:
- `auto`: tool làm hết.
- `assisted`: AI soạn, người duyệt.
- `manual`: còn làm tay.
- `checkpoint`: cố ý để người quyết, không tính vào %.

`ops` đếm bằng chứng thật cho từng việc rồi tính % tự động hóa. Bằng chứng gồm số lần chạy lệnh (bảng `usage_log`) và kết quả đã lưu (run, batch, điểm do code đếm, report, nhãn của người). Việc ghi `auto` mà không có log nào trong khoảng thời gian đang xét sẽ bị cảnh báo.

`usage_log` chỉ ghi tên lệnh, thời điểm, chạy được hay không, thời gian chạy và version. **Không ghi tham số**, vì tham số có thể chứa key hoặc token. Các lệnh chạy trước khi có log (29/10–02/11) được nạp lại một lần từ transcript Claude Code với `source = backfill:claude-transcript`, chỉ tính lệnh thật sự đã chạy. Lệnh người dùng tự gõ bằng `!` không có trong transcript, nên số này có thể thấp hơn thực tế.

## Feature đang đăng ký

| Repo | Feature | Adapter | Trạng thái |
|---|---|---|---|
| `english-tutor` | `lesson-flow` | `lesson-eval` | Rubric có sẵn, chờ CP3. Chấm log trong `auto_test_logs/` bằng `eval` (kèm `--config-file`) |
| `english-tutor` | `ai-character` | `chatbot-sim` | Chưa có rubric |
| `artimind` | `memory-chat` | `chatbot-sim` | Chưa có rubric |
| `mini-chatbot` | `refyn-plan` (ISI213) | `refyn-plan` | Đã duyệt, chấm bằng `run` |

### `refyn-plan` (ISI213 REFYN)

- Adapter `eval_agent/adapters/refyn_plan.py` gọi thẳng `generate_refyn_plan` của `AI-Mini-Chatbot` trong một subprocess, không cần bật server.
- Hồ sơ lấy từ `tests/refyn/profiles.py` của repo đó (10 nữ, 5 nam).
- Plan được rút gọn trước khi đưa cho judge (khoảng 126K → 16K ký tự): mỗi buổi một dòng, gồm tên bài và vùng tập.
- Rubric `eval/rubrics/refyn-plan/v1.1.0.yaml` chỉ chấm những gì phải đọc tên bài mới đánh giá được.
- Các luật cứng (28 buổi, cổng kiểm, thẻ tải khớp) để `step5_validate.py` và `tests/refyn/acceptance.py` kiểm bằng code.

## Độ tin cậy của judge

Agent chấm sản phẩm bằng LLM, nên phải đo được **chính judge** đáng tin tới đâu. Có hai phép đo:

### Độ ổn định (`stability`)

```bash
eval-agent stability --repo mini-chatbot --feature refyn-plan --runs 5
```

Lệnh này chấm lại các transcript **đã lưu** nhiều lần. Input không đổi, nên mọi dao động đo được là nhiễu của judge.
- Mỗi dimension có độ lệch chuẩn, khoảng dao động trung bình, và tỉ lệ item dao động từ 3 điểm trở lên.
- Đếm số item mà PASS/FAIL bị **lật** giữa các lần chấm đơn lẻ.

Điểm được lưu là median của `--runs` lần chấm. Phép đo này cho thấy vì sao không thể chỉ chấm một lần.

Kết quả thật trên ISI213: 14 transcript × 5 lần chấm. Hồ sơ thứ 15 (`bench-04`) bị loại vì một lần chấm trả JSON hỏng; lỗi này nay đã sửa, judge giữ lại các lần chấm thành công.

| Dimension | Độ lệch chuẩn TB | Khoảng dao động TB | Item dao động ≥ 3 điểm |
|---|---|---|---|
| `explain_truthfulness` | 0.19 | 0.4 | 7% |
| `focus_and_full_body_balance` | 0.36 | 0.9 | 7% |
| `profile_safety_judgement` | 0.33 | 0.9 | 14% |
| `progression_and_variety` | 0.42 | 1.0 | 0% |
| `session_part_fit` | 0.57 | 1.4 | 21% |

PASS/FAIL bị lật giữa các lần chấm đơn lẻ: 1/14 (`bench-m1`, điểm sát ngưỡng). `session_part_fit` dao động nhiều nhất, nên là dimension cần ưu tiên đối chiếu với người.

### Đối chiếu với người (`calibrate`)

```bash
eval-agent calibrate export --repo mini-chatbot --feature refyn-plan   # phiếu chấm tay, ẩn điểm judge
#  → người chấm điền human_score (0–10) vào labels.csv, theo GUIDE.md và items/*.md
eval-agent calibrate import calibration/<...>/labels.csv --by <tên>
eval-agent calibrate report --repo mini-chatbot --feature refyn-plan
```

Báo cáo từng dimension gồm:
- **MAE**
- **tỉ lệ lệch không quá ±2**
- **bias**: judge chấm cao hay thấp hơn người
- **Spearman**: judge có xếp hạng các item giống người không
- mức khớp **PASS/FAIL** trên những item đã chấm đủ mọi dimension

Một dimension được coi là **đáng tin** khi lệch không quá ±2 ở ít nhất 70% item, MAE ≤ 2, và Spearman ≥ 0.5. Dimension chưa đáng tin thì phải sửa câu chữ trong rubric hoặc prompt của judge, rồi đo lại.

### Kết quả đối chiếu trên ISI213 (người tham chiếu là model, chưa phải người)

Người tham chiếu ở đây là **3 agent Claude Opus độc lập**. Chúng chỉ được đọc `GUIDE.md` và file plan, không thấy điểm judge. Nhãn lưu dưới tên `claude-opus-blind`, tách khỏi nhãn của người. Phép đo này cho biết judge (Apero-LLM) lệch bao nhiêu so với một model mạnh hơn chấm độc lập. **Nó không thay được việc đối chiếu với người.**

| Dimension | MAE | Lệch ≤ ±2 | Bias (judge − tham chiếu) | Spearman | Đáng tin |
|---|---|---|---|---|---|
| `profile_safety_judgement` | 1.7 | 87% | +1.7 | 0.81 | ✅ |
| `explain_truthfulness` | 2.1 | 60% | +2.1 | 0.69 | ❌ |
| `session_part_fit` | 1.5 | 87% | +1.5 | 0.33 | ❌ |
| `focus_and_full_body_balance` | 1.5 | 80% | +1.3 | −0.39 | ❌ |
| `progression_and_variety` | 1.1 | 100% | +0.8 | −0.21 | ❌ |

PASS/FAIL khớp 15/15, nhưng con số này ít ý nghĩa vì cả hai bên đều chấm FAIL toàn bộ.

Phát hiện:
1. **Judge chấm rộng tay một cách có hệ thống.** Bias dương ở cả 5 dimension, từ +0.8 đến +2.1 điểm.
2. **`explain_truthfulness`: judge chỉ cho 5 hoặc 10.** Judge cho 10 ở `bench-m3` và `bench-m4`, trong khi explain nói "tránh bài nặng lưng/gối" mà plan lại có `mountain-climber`, `sumo-squat`, `scissor-kicks`. Tức là judge không đối chiếu từng câu explain với các buổi tập.
3. **Ba dimension còn lại bị nén thang điểm.** Judge chỉ dùng 2–4 (`session_part_fit`) hoặc 4–7 điểm, nên gần như không phân biệt được plan tốt với plan kém. Spearman thấp chủ yếu vì vậy.
4. **`profile_safety_judgement` đáng tin nhất**, nhưng có một lỗi: judge cho 10 ở `bench-01` (người chưa tập bao giờ, không đau khớp) dù plan có `walking-lunges` và `push-ups` ngay tuần 1. Rubric chỉ miễn trừ cho người *regular*, không miễn trừ cho người *never*.

**Rubric `v1.1.1`** (mốc điểm dựa trên đếm lỗi, đã duyệt CP3): kiểm bằng `calibrate try`, rồi chấm lại 15 plan. Plan giống hệt batch cũ, nên mọi thay đổi điểm đến từ rubric; `compare` đã cảnh báo đúng như vậy.

| Dimension | MAE v1.1.0 → v1.1.1 | Bias v1.1.0 → v1.1.1 |
|---|---|---|
| `session_part_fit` | 1.5 → 0.6 | +1.5 → +0.6 |
| `profile_safety_judgement` | 1.7 → 1.3 | +1.7 → −0.3 |
| `progression_and_variety` | 1.1 → 0.8 | +0.8 → −0.3 |
| `focus_and_full_body_balance` | 1.5 → 1.7 | +1.3 → +0.9 |
| `explain_truthfulness` | 2.1 → 2.1 | +2.1 → −1.4 (Spearman 0.83) |

Bias giảm ở mọi dimension. Điểm yếu mới: judge **bám vào các giá trị mốc**. Cả 8 hồ sơ có đau khớp đều nhận cùng bộ điểm 3/2/2/4/4, và 3 lần chấm cho ra kết quả giống hệt nhau. Vì vậy hai dimension `focus` và `progression` gần như không còn phân biệt được plan kém với plan rất kém (Spearman 0.28 và 0.0). Việc tiếp theo là cần người thật chấm một phần, và kiểm lại trên một batch mới.

### Truy vết

Mỗi run lưu kèm:
- `judge_prompt_hash`: version prompt của judge
- `rubric_hash`: version file rubric
- điểm của từng lần chấm (`run_scores`)

Khi điểm thay đổi, nhờ đó biết được là sản phẩm đổi, hay judge hoặc rubric đổi.

## Mức độ phù hợp với vị trí AI Engineer (A5)

Phần lõi của agent là việc của AI Engineer: đưa LLM vào một quy trình đánh giá, rồi **đo và kiểm soát được sai số của chính LLM đó**. CLI, SQLite, registry chỉ là phần khung.

| Việc | Ở đâu | Vì sao là việc của AI Engineer |
|---|---|---|
| LLM-as-judge chống bịa dẫn chứng | `judge.py` | Mỗi lỗi bị trừ phải trích nguyên văn, code kiểm lại, trích sai thì bắt chấm lại |
| Kiểm soát nhiễu của judge | `judge.py`, `stability` | Median nhiều lần chấm. Đo dao động và số lần PASS/FAIL bị lật trên input cố định |
| Đối chiếu judge với người | `calibration.py`, `calibrate` | MAE, ±2, bias, Spearman theo từng dimension, có ngưỡng "đáng tin". Phiếu chấm tay ẩn điểm judge để người chấm không bị ảnh hưởng |
| Từ spec ra tiêu chí đo được | `spec_parser.py`, CP1–CP3 | Tách phần AI khỏi PO spec, viết behavior kiểm tra được, soạn rubric. Người duyệt ở từng bước |
| Thiết kế rubric theo hệ thống thật | `eval/rubrics/refyn-plan/v1.1.0.yaml` | Chỉ chấm những gì code không kiểm được. Phần code kiểm được giao cho test |
| Thiết kế context cho judge | `adapters/refyn_plan.py` | Plan 126K ký tự rút còn 16K mà vẫn đủ thông tin để chấm |
| Phát hiện regression, truy vết, versioning | `store.py`, `compare` | Baseline theo từng hồ sơ. Version của tool, rubric và prompt judge gắn với mỗi run. Cảnh báo khi hai batch không so được với nhau |
| Phân tích lỗi có ràng buộc | `analyze`, `eval/context/<feature>.md` | Gom lỗi thành giả thuyết nguyên nhân, neo vào kiến trúc thật để không đoán mò. Người duyệt ở CP4 |
| Agent tool-calling có rào chắn | `agent.py` | LLM tự chọn bước. Danh sách tool cho phép, giới hạn chi phí, kiểm tham số trước khi chạy, lưu vết. Không thể vượt checkpoint |

Kết quả thật trên ISI213: 15 plan, 0/15 PASS. Agent tìm ra các lỗi có thật, như bài đặt sai phần buổi tập và bài không hợp người đau khớp, và report đã được gửi cho team sửa. Khi đo lại chính judge, agent phát hiện judge chấm rộng tay (+0.8 đến +2.1 điểm) và chỉ có 1/5 dimension đáng tin. Từ đó có việc cụ thể để sửa rubric (xem "Độ tin cậy của judge").

## Các module

| Module | File | Vai trò |
|---|---|---|
| CLI và workflow | `eval_agent/cli.py` | Lệnh, checkpoint, cổng chặn, `status`, `analyze`, `report`, `calibrate`, `stability` |
| Thư mục làm việc | `eval_agent/home.py` | Tìm thư mục làm việc, nạp `.env`, `init` |
| Registry | `eval_agent/registry.py` | repo → feature → version, stage, người duyệt |
| Spec → rubric | `eval_agent/spec_parser.py` | LLM lọc spec, viết AI spec, soạn rubric |
| Judge | `eval_agent/judge.py` | LLM-as-judge, kiểm câu trích, median, auto-fail, version của prompt |
| Độ tin cậy | `eval_agent/calibration.py` | Đối chiếu với người, đo độ ổn định |
| Adapter | `eval_agent/adapters/` | Sinh output của sản phẩm và chuyển thành transcript |
| Store | `eval_agent/store.py` | SQLite: run, batch, baseline, regression, report, nhãn người chấm |
| Agent | `eval_agent/agent.py` | Vòng lặp tool-calling, danh sách tool cho phép, giới hạn chi phí, nhật ký phiên |

## Chưa có

- Adapter cho chatbot (`chatbot-sim`) và lesson (`lesson-eval`): hiện phải tự đưa transcript cho `eval`.
- Trigger theo commit (webhook). Hiện chạy theo lịch bằng cron.
- Đọc PO spec từ AFFiNE.
- Chế độ chấm theo đáp án (accuracy / F1) cho chức năng phân loại, trích xuất.
