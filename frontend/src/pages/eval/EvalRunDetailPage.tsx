/**
 * EvalRunDetailPage — shows a single evaluation run with progress and results.
 */
import { CopyOutlined } from "@ant-design/icons";
import {
  Alert,
  Button,
  Card,
  Modal,
  Progress,
  Space,
  Spin,
  Statistic,
  Table,
  Tag,
  Tooltip,
  Typography,
  message,
} from "antd";
import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";

import { fetchEvalRun } from "@/api/eval";
import type { EvalCaseResult, EvalRunDetail } from "@/types/eval";

const { Text, Title } = Typography;

export default function EvalRunDetailPage() {
  const { runId } = useParams<{ runId: string }>();
  const [run, setRun] = useState<EvalRunDetail | null>(null);
  const [loading, setLoading] = useState(false);
  const [selected, setSelected] = useState<EvalCaseResult | null>(null);

  const load = async () => {
    if (!runId) return;
    setLoading(true);
    try {
      const detail = await fetchEvalRun(runId);
      setRun(detail);
    } catch (err) {
      // Silent fail; keep polling.
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void load();
  }, [runId]);

  useEffect(() => {
    if (!run || run.status !== "running") return;
    const timer = setInterval(() => {
      void load();
    }, 2000);
    return () => clearInterval(timer);
  }, [run]);

  if (!run) {
    return (
      <Card size="small">
        <Spin spinning style={{ display: "block", margin: "40px 0" }} />
      </Card>
    );
  }

  return (
    <Space direction="vertical" style={{ width: "100%" }} size="large">
      {run.error && (
        <Alert message="运行错误" description={run.error} type="error" showIcon />
      )}

      <Card size="small">
        <Space
          style={{ width: "100%", justifyContent: "space-between" }}
          align="center"
        >
          <Title level={5} style={{ margin: 0 }}>
            {run.dataset}
          </Title>
          <Space size="small">
            <Text type="secondary" style={{ fontSize: 12 }}>
              {run.run_id}
            </Text>
            <Tooltip title="复制运行 ID">
              <Button
                size="small"
                type="text"
                icon={<CopyOutlined />}
                onClick={() => {
                  void navigator.clipboard.writeText(run.run_id).then(() => {
                    message.success("运行 ID 已复制");
                  });
                }}
              />
            </Tooltip>
          </Space>
        </Space>
        <Progress
          percent={
            run.total ? Math.round((run.processed / run.total) * 100) : 0
          }
          status={
            run.status === "failed"
              ? "exception"
              : run.status === "completed"
                ? "success"
                : "active"
          }
          showInfo
        />
        <Space wrap>
          <Statistic title="总数" value={run.total} valueStyle={{ fontSize: 14 }} />
          <Statistic title="通过" value={run.passed} valueStyle={{ fontSize: 14 }} />
          <Statistic title="失败" value={run.errored} valueStyle={{ fontSize: 14 }} />
          <Statistic
            title="准确率"
            value={Math.round((run.metrics.accuracy || 0) * 100)}
            suffix="%"
            valueStyle={{ fontSize: 14 }}
          />
          {run.metrics.tool_call_accuracy !== undefined && (
            <Statistic
              title="工具调用准确率"
              value={Math.round((run.metrics.tool_call_accuracy ?? 0) * 100)}
              suffix="%"
              valueStyle={{ fontSize: 14 }}
            />
          )}
          {run.metrics.relevance_accuracy !== undefined && (
            <Statistic
              title="相关性准确率"
              value={Math.round((run.metrics.relevance_accuracy ?? 0) * 100)}
              suffix="%"
              valueStyle={{ fontSize: 14 }}
            />
          )}
        </Space>
      </Card>

      <Card title="Case 结果" size="small" loading={loading && run.status === "running"}>
        <Table
          size="small"
          rowKey="result_id"
          pagination={{ pageSize: 10 }}
          dataSource={run.results}
          expandable={{
            expandedRowRender: (record: EvalCaseResult) => (
              <div style={{ margin: 0 }}>
                {record.error && (
                  <Alert
                    message="执行错误"
                    description={record.error}
                    type="error"
                    style={{ marginBottom: 8 }}
                  />
                )}
                {Object.entries(record.scores).map(([name, score]) => (
                  <div key={name} style={{ marginBottom: 8 }}>
                    <Text strong>{name}</Text>
                    <br />
                    <Text type={score.passed ? "success" : "danger"}>
                      {score.passed ? "通过" : "未通过"}: {score.reason}
                    </Text>
                  </div>
                ))}
                {record.gold_sql && (
                  <div style={{ marginBottom: 8 }}>
                    <Text strong type="secondary">
                      标准答案 SQL
                    </Text>
                    <pre
                      style={{
                        whiteSpace: "pre-wrap",
                        wordBreak: "break-word",
                        background: "#f6ffed",
                        padding: 8,
                        borderRadius: 4,
                      }}
                    >
                      {record.gold_sql}
                    </pre>
                  </div>
                )}
                {record.generated_sql && (
                  <div style={{ marginBottom: 8 }}>
                    <Text strong type="secondary">
                      生成 SQL
                    </Text>
                    <pre
                      style={{
                        whiteSpace: "pre-wrap",
                        wordBreak: "break-word",
                        background: "#e6f4ff",
                        padding: 8,
                        borderRadius: 4,
                      }}
                    >
                      {record.generated_sql}
                    </pre>
                  </div>
                )}
                {!record.gold_sql && !record.generated_sql && record.answer && (
                  <div
                    className="markdown-body"
                    style={{ maxHeight: 240, overflow: "auto" }}
                  >
                    <pre
                      style={{
                        whiteSpace: "pre-wrap",
                        wordBreak: "break-word",
                      }}
                    >
                      {record.answer}
                    </pre>
                  </div>
                )}
              </div>
            ),
          }}
          columns={[
            {
              title: "#",
              dataIndex: "case_index",
              key: "case_index",
              width: 50,
            },
            {
              title: "问题",
              dataIndex: "question",
              key: "question",
              ellipsis: true,
              render: (text: string) => (
                <Tooltip title={text} placement="topLeft">
                  <span>{text}</span>
                </Tooltip>
              ),
            },
            {
              title: "结果",
              dataIndex: "passed",
              key: "passed",
              width: 80,
              render: (passed: boolean) => (
                <Tag color={passed ? "success" : "error"}>
                  {passed ? "通过" : "未通过"}
                </Tag>
              ),
            },
            {
              title: "提示词",
              key: "prompt",
              width: 100,
              render: (_: unknown, record: EvalCaseResult) => (
                <Button size="small" onClick={() => setSelected(record)}>
                  查看 Prompt
                </Button>
              ),
            },
          ]}
        />
      </Card>

      <Modal
        title={`Case #${selected?.case_index ?? ""} — 最终提示词与检索上下文`}
        open={!!selected}
        onCancel={() => setSelected(null)}
        footer={null}
        width={900}
      >
        {selected && (
          <Space direction="vertical" style={{ width: "100%" }}>
            {selected.thread_id && (
              <div>
                <Text strong>Thread ID</Text>
                <pre
                  style={{
                    whiteSpace: "pre-wrap",
                    wordBreak: "break-word",
                    background: "#f5f5f5",
                    padding: 8,
                    borderRadius: 4,
                    maxHeight: 120,
                    overflow: "auto",
                  }}
                >
                  {selected.thread_id}
                </pre>
              </div>
            )}

            {selected.retrieved_context && (
              <div>
                <Text strong>检索到的语义层片段（retrieved_context）</Text>
                <pre
                  style={{
                    whiteSpace: "pre-wrap",
                    wordBreak: "break-word",
                    background: "#fffbe6",
                    padding: 8,
                    borderRadius: 4,
                    maxHeight: 240,
                    overflow: "auto",
                  }}
                >
                  {selected.retrieved_context}
                </pre>
              </div>
            )}

            {selected.user_message ? (
              <div>
                <Text strong>最终 user_message（Agent 实际收到的 prompt）</Text>
                <pre
                  style={{
                    whiteSpace: "pre-wrap",
                    wordBreak: "break-word",
                    background: "#e6f4ff",
                    padding: 8,
                    borderRadius: 4,
                    maxHeight: 480,
                    overflow: "auto",
                  }}
                >
                  {selected.user_message}
                </pre>
              </div>
            ) : (
              <Alert message="该 case 没有保存 prompt（可能是旧 run 或未启用评测持久化）" type="info" showIcon />
            )}
          </Space>
        )}
      </Modal>
    </Space>
  );
}
