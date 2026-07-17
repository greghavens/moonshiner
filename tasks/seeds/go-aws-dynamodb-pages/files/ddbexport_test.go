// Acceptance tests for the ddbexport telemetry exporter.
//
// The exporter runs against a fake that implements its DynamoDB interface
// with the real AWS SDK for Go v2 input/output/types values, so every
// request shape asserted here is the genuine wire contract pinned in
// docs/contract.json. No network, no real credentials.
package ddbexport_test

import (
	"context"
	"encoding/json"
	"errors"
	"os"
	"reflect"
	"testing"
	"time"

	"github.com/aws/aws-sdk-go-v2/aws"
	"github.com/aws/aws-sdk-go-v2/service/dynamodb"
	"github.com/aws/aws-sdk-go-v2/service/dynamodb/types"

	ddbexport "go-aws-dynamodb-pages"
)

// The real SDK client must satisfy the exporter's interface.
var _ ddbexport.DynamoAPI = (*dynamodb.Client)(nil)

const (
	srcTable = "telemetry-readings"
	dstTable = "telemetry-archive"
	device   = "dev-42"
	since    = int64(1700000000)
)

type contract struct {
	Query struct {
		KeyConditionExpression string `json:"key_condition_expression"`
	} `json:"query"`
	BatchWriteItem struct {
		MaxItemsPerCall int `json:"max_items_per_call"`
	} `json:"batch_write_item"`
}

func loadContract(t *testing.T) contract {
	t.Helper()
	raw, err := os.ReadFile("docs/contract.json")
	if err != nil {
		t.Fatalf("read docs/contract.json: %v", err)
	}
	var c contract
	if err := json.Unmarshal(raw, &c); err != nil {
		t.Fatalf("parse docs/contract.json: %v", err)
	}
	return c
}

func item(ts int64, metric string, value string) map[string]types.AttributeValue {
	return map[string]types.AttributeValue{
		"pk":     &types.AttributeValueMemberS{Value: device},
		"sk":     &types.AttributeValueMemberN{Value: itoa(ts)},
		"metric": &types.AttributeValueMemberS{Value: metric},
		"value":  &types.AttributeValueMemberN{Value: value},
	}
}

func itoa(v int64) string {
	b := []byte{}
	if v == 0 {
		return "0"
	}
	for v > 0 {
		b = append([]byte{byte('0' + v%10)}, b...)
		v /= 10
	}
	return string(b)
}

func key(ts int64) map[string]types.AttributeValue {
	return map[string]types.AttributeValue{
		"pk": &types.AttributeValueMemberS{Value: device},
		"sk": &types.AttributeValueMemberN{Value: itoa(ts)},
	}
}

type fakeDDB struct {
	t           *testing.T
	queryPages  []*dynamodb.QueryOutput
	queryErr    error // returned on the first Query call when set
	queryInputs []dynamodb.QueryInput
	batchSteps  []func(*dynamodb.BatchWriteItemInput) (*dynamodb.BatchWriteItemOutput, error)
	batchInputs []dynamodb.BatchWriteItemInput
}

func (f *fakeDDB) Query(_ context.Context, in *dynamodb.QueryInput, _ ...func(*dynamodb.Options)) (*dynamodb.QueryOutput, error) {
	f.queryInputs = append(f.queryInputs, *in)
	if f.queryErr != nil {
		err := f.queryErr
		f.queryErr = nil
		return nil, err
	}
	i := len(f.queryInputs) - 1
	if i >= len(f.queryPages) {
		f.t.Fatalf("unexpected Query call #%d", i+1)
	}
	return f.queryPages[i], nil
}

func (f *fakeDDB) BatchWriteItem(_ context.Context, in *dynamodb.BatchWriteItemInput, _ ...func(*dynamodb.Options)) (*dynamodb.BatchWriteItemOutput, error) {
	f.batchInputs = append(f.batchInputs, *in)
	i := len(f.batchInputs) - 1
	if i >= len(f.batchSteps) {
		f.t.Fatalf("unexpected BatchWriteItem call #%d", i+1)
	}
	return f.batchSteps[i](in)
}

func acceptAll(in *dynamodb.BatchWriteItemInput) (*dynamodb.BatchWriteItemOutput, error) {
	return &dynamodb.BatchWriteItemOutput{
		UnprocessedItems: map[string][]types.WriteRequest{},
		ConsumedCapacity: []types.ConsumedCapacity{
			{TableName: aws.String(dstTable), CapacityUnits: aws.Float64(float64(len(in.RequestItems[dstTable])))},
		},
	}, nil
}

func singlePage(items []map[string]types.AttributeValue) []*dynamodb.QueryOutput {
	return []*dynamodb.QueryOutput{{
		Items:            items,
		Count:            int32(len(items)),
		ConsumedCapacity: &types.ConsumedCapacity{TableName: aws.String(srcTable), CapacityUnits: aws.Float64(0.5)},
	}}
}

func opts(sleeps *[]time.Duration) ddbexport.Options {
	return ddbexport.Options{
		PageLimit:       250,
		MaxBatchRetries: 3,
		BaseDelay:       100 * time.Millisecond,
		MaxDelay:        400 * time.Millisecond,
		Sleep:           func(d time.Duration) { *sleeps = append(*sleeps, d) },
	}
}

// ------------------------------------------------------------- query input

func TestBuildQueryInputMarshalsExpressionValues(t *testing.T) {
	c := loadContract(t)
	in, err := ddbexport.BuildQueryInput(srcTable, device, since, 250)
	if err != nil {
		t.Fatalf("BuildQueryInput: %v", err)
	}
	if got := aws.ToString(in.TableName); got != srcTable {
		t.Fatalf("TableName = %q, want %q", got, srcTable)
	}
	if got := aws.ToString(in.KeyConditionExpression); got != c.Query.KeyConditionExpression {
		t.Fatalf("KeyConditionExpression = %q, want %q", got, c.Query.KeyConditionExpression)
	}
	dev, ok := in.ExpressionAttributeValues[":device"].(*types.AttributeValueMemberS)
	if !ok || dev.Value != device {
		t.Fatalf(":device = %#v, want S %q", in.ExpressionAttributeValues[":device"], device)
	}
	s, ok := in.ExpressionAttributeValues[":since"].(*types.AttributeValueMemberN)
	if !ok || s.Value != "1700000000" {
		t.Fatalf(":since = %#v, want N \"1700000000\"", in.ExpressionAttributeValues[":since"])
	}
	if in.ReturnConsumedCapacity != types.ReturnConsumedCapacityTotal {
		t.Fatalf("ReturnConsumedCapacity = %v, want TOTAL", in.ReturnConsumedCapacity)
	}
	if in.Limit == nil || *in.Limit != 250 {
		t.Fatalf("Limit = %v, want 250", in.Limit)
	}

	noLimit, err := ddbexport.BuildQueryInput(srcTable, device, since, 0)
	if err != nil {
		t.Fatalf("BuildQueryInput(limit=0): %v", err)
	}
	if noLimit.Limit != nil {
		t.Fatalf("Limit must be omitted when 0, got %v", *noLimit.Limit)
	}
}

// ------------------------------------------------------------- pagination

func TestExportFollowsLastEvaluatedKeyUntilEmpty(t *testing.T) {
	var sleeps []time.Duration
	lek1 := key(1700000100)
	lek2 := key(1700000200)
	fake := &fakeDDB{
		t: t,
		queryPages: []*dynamodb.QueryOutput{
			{
				Items:            []map[string]types.AttributeValue{item(1700000050, "temp_c", "21.5"), item(1700000100, "temp_c", "21.9")},
				LastEvaluatedKey: lek1,
				ConsumedCapacity: &types.ConsumedCapacity{TableName: aws.String(srcTable), CapacityUnits: aws.Float64(1.5)},
			},
			{
				// Empty intermediate page: pagination must continue anyway.
				Items:            nil,
				LastEvaluatedKey: lek2,
				ConsumedCapacity: &types.ConsumedCapacity{TableName: aws.String(srcTable), CapacityUnits: aws.Float64(2)},
			},
			{
				Items: []map[string]types.AttributeValue{item(1700000300, "rh_pct", "40")},
				// No LastEvaluatedKey and no ConsumedCapacity: last page.
			},
		},
		batchSteps: []func(*dynamodb.BatchWriteItemInput) (*dynamodb.BatchWriteItemOutput, error){acceptAll},
	}
	rep, err := ddbexport.New(fake, opts(&sleeps)).Export(context.Background(), srcTable, dstTable, device, since)
	if err != nil {
		t.Fatalf("Export: %v", err)
	}
	if len(fake.queryInputs) != 3 {
		t.Fatalf("Query calls = %d, want 3", len(fake.queryInputs))
	}
	if fake.queryInputs[0].ExclusiveStartKey != nil {
		t.Fatalf("first page must not set ExclusiveStartKey")
	}
	if !reflect.DeepEqual(fake.queryInputs[1].ExclusiveStartKey, lek1) {
		t.Fatalf("page 2 ExclusiveStartKey = %#v, want page 1 LastEvaluatedKey", fake.queryInputs[1].ExclusiveStartKey)
	}
	if !reflect.DeepEqual(fake.queryInputs[2].ExclusiveStartKey, lek2) {
		t.Fatalf("page 3 ExclusiveStartKey = %#v, want page 2 LastEvaluatedKey", fake.queryInputs[2].ExclusiveStartKey)
	}
	if rep.Pages != 3 || rep.ItemsRead != 3 {
		t.Fatalf("Pages=%d ItemsRead=%d, want 3 and 3", rep.Pages, rep.ItemsRead)
	}
	if rep.ReadCapacity != 3.5 {
		t.Fatalf("ReadCapacity = %v, want 3.5 (nil-safe sum)", rep.ReadCapacity)
	}
	if rep.ItemsWritten != 3 {
		t.Fatalf("ItemsWritten = %d, want 3", rep.ItemsWritten)
	}
	if len(sleeps) != 0 {
		t.Fatalf("no backoff expected on the happy path, got %v", sleeps)
	}
}

func TestQueryErrorPropagatesWithEmptyReport(t *testing.T) {
	var sleeps []time.Duration
	fake := &fakeDDB{t: t, queryErr: &types.ResourceNotFoundException{Message: aws.String("no such table")}}
	rep, err := ddbexport.New(fake, opts(&sleeps)).Export(context.Background(), srcTable, dstTable, device, since)
	var rnf *types.ResourceNotFoundException
	if !errors.As(err, &rnf) {
		t.Fatalf("want ResourceNotFoundException, got %v", err)
	}
	if rep.Pages != 0 || rep.ItemsRead != 0 || rep.ItemsWritten != 0 {
		t.Fatalf("report must be empty on immediate query failure, got %+v", rep)
	}
	if len(fake.batchInputs) != 0 {
		t.Fatalf("no writes may happen after a failed query")
	}
}

// ---------------------------------------------------------------- batching

func TestBatchWritesChunkAtDocumentedMaximum(t *testing.T) {
	c := loadContract(t)
	if ddbexport.MaxBatchSize != c.BatchWriteItem.MaxItemsPerCall {
		t.Fatalf("MaxBatchSize = %d, want the documented %d", ddbexport.MaxBatchSize, c.BatchWriteItem.MaxItemsPerCall)
	}
	items := make([]map[string]types.AttributeValue, 0, 60)
	for i := int64(0); i < 60; i++ {
		items = append(items, item(1700000000+i, "temp_c", "20"))
	}
	var sleeps []time.Duration
	fake := &fakeDDB{
		t:          t,
		queryPages: singlePage(items),
		batchSteps: []func(*dynamodb.BatchWriteItemInput) (*dynamodb.BatchWriteItemOutput, error){acceptAll, acceptAll, acceptAll},
	}
	rep, err := ddbexport.New(fake, opts(&sleeps)).Export(context.Background(), srcTable, dstTable, device, since)
	if err != nil {
		t.Fatalf("Export: %v", err)
	}
	if len(fake.batchInputs) != 3 {
		t.Fatalf("BatchWriteItem calls = %d, want 3 (25/25/10)", len(fake.batchInputs))
	}
	sizes := []int{}
	for _, in := range fake.batchInputs {
		if len(in.RequestItems) != 1 {
			t.Fatalf("RequestItems must target exactly the archive table, got %d tables", len(in.RequestItems))
		}
		reqs := in.RequestItems[dstTable]
		sizes = append(sizes, len(reqs))
		if in.ReturnConsumedCapacity != types.ReturnConsumedCapacityTotal {
			t.Fatalf("BatchWriteItem ReturnConsumedCapacity = %v, want TOTAL", in.ReturnConsumedCapacity)
		}
		for _, r := range reqs {
			if r.PutRequest == nil || r.PutRequest.Item == nil {
				t.Fatalf("every write must be a PutRequest with an Item")
			}
		}
	}
	if !reflect.DeepEqual(sizes, []int{25, 25, 10}) {
		t.Fatalf("chunk sizes = %v, want [25 25 10]", sizes)
	}
	first := fake.batchInputs[0].RequestItems[dstTable][0].PutRequest.Item
	if !reflect.DeepEqual(first, items[0]) {
		t.Fatalf("written item differs from the read item:\n%#v\n%#v", first, items[0])
	}
	if rep.ItemsWritten != 60 || rep.BatchCalls != 3 {
		t.Fatalf("ItemsWritten=%d BatchCalls=%d, want 60 and 3", rep.ItemsWritten, rep.BatchCalls)
	}
	if rep.WriteCapacity != 60 {
		t.Fatalf("WriteCapacity = %v, want 60 (summed per-table list)", rep.WriteCapacity)
	}
}

func TestUnprocessedItemsAreRetriedAloneAndSuccessesPreserved(t *testing.T) {
	items := []map[string]types.AttributeValue{
		item(1700000001, "temp_c", "20"),
		item(1700000002, "temp_c", "21"),
		item(1700000003, "temp_c", "22"),
		item(1700000004, "temp_c", "23"),
	}
	leftover := []types.WriteRequest{
		{PutRequest: &types.PutRequest{Item: items[1]}},
		{PutRequest: &types.PutRequest{Item: items[3]}},
	}
	var sleeps []time.Duration
	fake := &fakeDDB{
		t:          t,
		queryPages: singlePage(items),
		batchSteps: []func(*dynamodb.BatchWriteItemInput) (*dynamodb.BatchWriteItemOutput, error){
			func(in *dynamodb.BatchWriteItemInput) (*dynamodb.BatchWriteItemOutput, error) {
				if len(in.RequestItems[dstTable]) != 4 {
					t.Fatalf("first call must carry all 4 items")
				}
				return &dynamodb.BatchWriteItemOutput{
					UnprocessedItems: map[string][]types.WriteRequest{dstTable: leftover},
				}, nil
			},
			func(in *dynamodb.BatchWriteItemInput) (*dynamodb.BatchWriteItemOutput, error) {
				if !reflect.DeepEqual(in.RequestItems[dstTable], leftover) {
					t.Fatalf("retry must resend exactly the unprocessed entries, got %#v", in.RequestItems[dstTable])
				}
				return &dynamodb.BatchWriteItemOutput{UnprocessedItems: map[string][]types.WriteRequest{}}, nil
			},
		},
	}
	rep, err := ddbexport.New(fake, opts(&sleeps)).Export(context.Background(), srcTable, dstTable, device, since)
	if err != nil {
		t.Fatalf("Export: %v", err)
	}
	if rep.ItemsWritten != 4 || rep.BatchCalls != 2 || rep.Unprocessed != 0 {
		t.Fatalf("got %+v, want 4 written over 2 calls with nothing left", rep)
	}
	if !reflect.DeepEqual(sleeps, []time.Duration{100 * time.Millisecond}) {
		t.Fatalf("sleeps = %v, want one base delay before the retry", sleeps)
	}
}

func TestUnprocessedRetriesAreBoundedWithBackoffCap(t *testing.T) {
	items := []map[string]types.AttributeValue{
		item(1700000001, "temp_c", "20"),
		item(1700000002, "temp_c", "21"),
		item(1700000003, "temp_c", "22"),
	}
	stuck := []types.WriteRequest{{PutRequest: &types.PutRequest{Item: items[2]}}}
	always := func(in *dynamodb.BatchWriteItemInput) (*dynamodb.BatchWriteItemOutput, error) {
		return &dynamodb.BatchWriteItemOutput{
			UnprocessedItems: map[string][]types.WriteRequest{dstTable: stuck},
		}, nil
	}
	var sleeps []time.Duration
	fake := &fakeDDB{
		t:          t,
		queryPages: singlePage(items),
		batchSteps: []func(*dynamodb.BatchWriteItemInput) (*dynamodb.BatchWriteItemOutput, error){always, always, always, always},
	}
	rep, err := ddbexport.New(fake, opts(&sleeps)).Export(context.Background(), srcTable, dstTable, device, since)
	if !errors.Is(err, ddbexport.ErrUnprocessed) {
		t.Fatalf("want ErrUnprocessed, got %v", err)
	}
	if rep.BatchCalls != 4 { // initial call + MaxBatchRetries retries
		t.Fatalf("BatchCalls = %d, want 4", rep.BatchCalls)
	}
	if rep.ItemsWritten != 2 || rep.Unprocessed != 1 {
		t.Fatalf("successful writes must be preserved: got written=%d unprocessed=%d, want 2 and 1", rep.ItemsWritten, rep.Unprocessed)
	}
	want := []time.Duration{100 * time.Millisecond, 200 * time.Millisecond, 400 * time.Millisecond}
	if !reflect.DeepEqual(sleeps, want) {
		t.Fatalf("backoff = %v, want doubling capped schedule %v", sleeps, want)
	}
}

func TestWholeCallThroughputErrorRetriesFullBatch(t *testing.T) {
	items := []map[string]types.AttributeValue{
		item(1700000001, "temp_c", "20"),
		item(1700000002, "temp_c", "21"),
	}
	var sleeps []time.Duration
	fake := &fakeDDB{
		t:          t,
		queryPages: singlePage(items),
		batchSteps: []func(*dynamodb.BatchWriteItemInput) (*dynamodb.BatchWriteItemOutput, error){
			func(*dynamodb.BatchWriteItemInput) (*dynamodb.BatchWriteItemOutput, error) {
				return nil, &types.ProvisionedThroughputExceededException{Message: aws.String("throttled")}
			},
			func(in *dynamodb.BatchWriteItemInput) (*dynamodb.BatchWriteItemOutput, error) {
				if len(in.RequestItems[dstTable]) != 2 {
					t.Fatalf("full batch must be resent after a whole-call throttle")
				}
				return acceptAll(in)
			},
		},
	}
	rep, err := ddbexport.New(fake, opts(&sleeps)).Export(context.Background(), srcTable, dstTable, device, since)
	if err != nil {
		t.Fatalf("Export: %v", err)
	}
	if rep.ItemsWritten != 2 || rep.BatchCalls != 2 {
		t.Fatalf("got %+v, want both items written on the second call", rep)
	}
	if !reflect.DeepEqual(sleeps, []time.Duration{100 * time.Millisecond}) {
		t.Fatalf("sleeps = %v, want one base delay", sleeps)
	}
}

func TestTerminalBatchErrorStopsAndPreservesReport(t *testing.T) {
	items := make([]map[string]types.AttributeValue, 0, 30)
	for i := int64(0); i < 30; i++ {
		items = append(items, item(1700000000+i, "temp_c", "20"))
	}
	var sleeps []time.Duration
	fake := &fakeDDB{
		t:          t,
		queryPages: singlePage(items),
		batchSteps: []func(*dynamodb.BatchWriteItemInput) (*dynamodb.BatchWriteItemOutput, error){
			acceptAll,
			func(*dynamodb.BatchWriteItemInput) (*dynamodb.BatchWriteItemOutput, error) {
				return nil, &types.ResourceNotFoundException{Message: aws.String("archive table dropped")}
			},
		},
	}
	rep, err := ddbexport.New(fake, opts(&sleeps)).Export(context.Background(), srcTable, dstTable, device, since)
	var rnf *types.ResourceNotFoundException
	if !errors.As(err, &rnf) {
		t.Fatalf("want ResourceNotFoundException, got %v", err)
	}
	if rep.ItemsWritten != 25 {
		t.Fatalf("first chunk's successful writes must be preserved, got %d", rep.ItemsWritten)
	}
	if len(sleeps) != 0 {
		t.Fatalf("terminal errors must not be retried, got sleeps %v", sleeps)
	}
}

// ---------------------------------------------------------------- decoding

func TestDecodeReadingsUsesDynamodbavTags(t *testing.T) {
	items := []map[string]types.AttributeValue{
		item(1700000050, "temp_c", "21.5"),
		item(1700000100, "rh_pct", "40"),
	}
	items[1]["extra"] = &types.AttributeValueMemberBOOL{Value: true} // unknown attrs ignored
	readings, err := ddbexport.DecodeReadings(items)
	if err != nil {
		t.Fatalf("DecodeReadings: %v", err)
	}
	want := []ddbexport.Reading{
		{Device: device, TS: 1700000050, Metric: "temp_c", Value: 21.5},
		{Device: device, TS: 1700000100, Metric: "rh_pct", Value: 40},
	}
	if !reflect.DeepEqual(readings, want) {
		t.Fatalf("readings = %+v, want %+v", readings, want)
	}
}
