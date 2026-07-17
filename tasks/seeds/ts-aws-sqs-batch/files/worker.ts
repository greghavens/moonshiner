// Original single-message worker for the render-job queue. It drains one
// message per call: receive with long polling, run the handler, delete.
// Kept working as-is while the batch worker is built alongside it.
import {
  DeleteMessageCommand,
  ReceiveMessageCommand,
  type Message,
} from "@aws-sdk/client-sqs";

export interface SqsSender {
  send(command: any): Promise<any>;
}

export type Handler = (message: Message) => void | Promise<void>;

export async function processOne(
  client: SqsSender,
  queueUrl: string,
  handler: Handler,
): Promise<boolean> {
  const out = await client.send(
    new ReceiveMessageCommand({
      QueueUrl: queueUrl,
      MaxNumberOfMessages: 1,
      WaitTimeSeconds: 20,
    }),
  );
  const message: Message | undefined = out.Messages?.[0];
  if (!message) {
    return false;
  }
  await handler(message);
  await client.send(
    new DeleteMessageCommand({
      QueueUrl: queueUrl,
      ReceiptHandle: message.ReceiptHandle,
    }),
  );
  return true;
}
