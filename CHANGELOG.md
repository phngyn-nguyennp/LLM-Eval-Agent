# Changelog

Version của tool nằm ở `eval_agent/__version__`, kiểm bằng `eval-agent --version`. Mỗi run lưu kèm version này, nên `batches` và `compare` biết một batch được chấm bằng bản nào.

## 0.6.0 — 2026-11-04

- **`eval-agent ops`:** danh mục việc lặp lại (`ops.yaml`), mỗi việc đếm bằng chứng từ log, và tính % tự động hóa (`checkpoint` không tính).
- **`usage_log`:** mỗi lệnh CLI ghi tên lệnh, chạy được hay không, thời gian chạy, version. Không ghi tham số.
- `run --target-url / --target-key`: lấy output từ server đã deploy (ví dụ dev) thay vì chạy code. Profile và nhãn lấy từ `--repo-path`.

## 0.5.1 — 2026-11-02

- **Không còn âm thầm thiếu lượt chấm.** Trước đây một lượt judge hỏng (không ra JSON hợp lệ sau 3 lần thử, hoặc lỗi API) bị bỏ qua, và median chỉ lấy từ 1–2 lượt mà không báo. Ở ISI213, 3–7/15 item mỗi batch bị như vậy; PASS của `bench-09` (0.90 → 0.45 trên cùng một plan) là từ 1 lượt chấm.
  - Lượt hỏng giờ được chấm lại cho đủ `--runs`. Quá `--runs` lượt hỏng thì giữ số lượt có được.
  - `run` in danh sách item thiếu lượt kèm lỗi cuối cùng. `compare` có cột Runs (A/B) và cảnh báo item nào dưới 3 lượt.
  - Prompt judge giữ nguyên (`7dee392b9fe4`), nên vẫn so được với batch cũ. Lỗi gốc ở plan dài là judge viết rationale rất dài hoặc lặp ký tự tab tới hết 8192 token; thêm giới hạn độ dài vào prompt chỉ nâng tỉ lệ parse từ 3/12 lên 5/12, không đủ để đổi prompt.

## 0.5.0 — 2026-10-30

- **Tiêu chí chấm bằng code.** Rubric có thể ghi `computed: <check>` cho một dimension; judge sẽ bỏ qua dimension đó và code chấm (`eval_agent/checks.py`). Điểm này giống hệt nhau qua các lần chấm, nên `stability` đọc độ nhiễu bằng 0.
- **`refyn_part_fit`** chấm `session_part_fit` của ISI213 dựa trên nhãn role:
  - Bài tính là sai chỗ: str/cardio nằm trong warm-up hoặc cool-down, stretch nằm trong main của buổi A/B, hoặc str/cardio xuất hiện trong buổi C.
  - Số bài sai được quy ra điểm theo đúng các mốc của rubric.
  - Lý do bỏ judge: mỗi plan có khoảng 500 bài, judge không đếm nổi, và cho khoảng 2 điểm cho cả plan có 7 bài sai lẫn plan có 140 bài sai.
- `run --allow-draft` chấm bằng bản nháp mới nhất đang chờ duyệt, kể cả khi đã có một version khác đang active.
- Khi mọi item đều lỗi, `run` không còn gợi ý đặt batch rỗng làm baseline.
- Thêm `tests/` (pytest).

## 0.4.0 — 2026-10-30

- **`run --repo-path`**: chấm code trên một checkout khác (ví dụ worktree của nhánh đang sửa) mà không đổi registry; output in branch/commit được chấm.
- **Ghi định dạng input của judge.** Mỗi run lưu `input_format`. `batches` có thêm cột Input, và `compare` báo "not like-for-like" khi format khác nhau.
- **Adapter ISI213 (`refyn-plan/2-labels`):**
  - Chạy được với cả code 1.0.0 lẫn 1.1.0.
  - Đưa `explain_intro` vào transcript.
  - Gắn nhãn catalog sau mỗi bài: role; tải trên khớp đang đau (chỉ khi `mod`/`high`); impact; kneel. Judge được dặn rằng nhãn có thể sai.
  - Bỏ mã video trong tên bài để transcript ngắn lại.
  - Trước khi có nhãn, judge chỉ thấy tên bài nên không đo được thay đổi về role. Với nhãn, `session_part_fit` của bản sửa ISI213 tăng 2.1 → 2.9.
- **Sửa lỗi baseline:** ràng buộc `UNIQUE(..., active)` chặn đổi baseline lần thứ hai cho cùng item. Thay bằng partial unique index (một baseline active mỗi item); DB cũ được migrate tự động.

## 0.3.0 — 2026-10-29

- **Versioning.** Thêm các lệnh `rubric versions`, `rubric new-version`, `rubric diff`, `batches`, `compare`, và `calibrate try` (kiểm bản nháp rubric so với nhãn tham chiếu trước khi duyệt).
  - Version mới chỉ thành active khi được duyệt ở CP3. Bản cũ vẫn chấm được trong lúc chờ, duyệt xong mới bị archive.
  - Mỗi run lưu `tool_version`, `rubric_hash`, `judge_prompt_hash`.
  - Report của `analyze` và nhật ký agent ghi lại version của prompt.
  - `compare` cảnh báo khi hai batch được chấm bằng rubric hoặc judge khác nhau.
- **Chế độ agent** (`eval-agent agent`): LLM tự chọn tool trong một danh sách được phép, có giới hạn chi phí và nhật ký phiên. Không có tool nào vượt được checkpoint.
- **Độ tin cậy của judge:**
  - `stability` chấm lại cùng transcript nhiều lần để đo nhiễu của judge.
  - `calibrate export / import / report` đối chiếu judge với người chấm (MAE, ±2, bias, Spearman), phiếu chấm tay không hiện điểm judge.
- **Judge:** một lần chấm hỏng không còn làm mất cả item.

## 0.2.0 — 2026-10-29

- **Đóng gói:** cài bằng `pip`, lệnh `eval-agent`, thư mục làm việc `EVAL_AGENT_HOME`, lệnh `init`, file `.env.example`.
- **Checkpoint:**
  - CP1–CP3 (scope, AI spec, rubric) qua lệnh `approve`.
  - `eval` / `run` bị chặn khi rubric chưa duyệt (`--allow-draft` cho chạy thử, không lưu).
  - Quyết định baseline theo batch.
  - CP4: `analyze` → `report approve` → `report send`, kèm kiểm tra hash nội dung report.
- **`status`:** cho biết việc gì đang chờ người.
- **Adapter `refyn-plan`** cho ISI213, lệnh `run`.

## 0.1.0

- Pipeline PO spec → AI spec → rubric, LLM-as-judge có kiểm câu trích, lưu kết quả vào SQLite, baseline và phát hiện regression.
