"""R4-04B real embedding preparation and isolated, cache-only retrieval evaluation.

Fresh processes, USE_TORCH=0 USE_TF=0 HF_HUB_OFFLINE=1:
python -m eval.r4.hybrid_experiments prepare
python -m eval.r4.hybrid_experiments evaluate
"""
from contextlib import closing
import array
import argparse
import base64
from datetime import datetime, timezone
from dataclasses import replace
import gc
import json
import math
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import time
from unittest.mock import patch

from eval.r4.dataset import DIRECTORY, ROOT, sha256
from eval.r4.gold import load_gold, verify_files
from eval.r4.metrics import macro_average
from eval.r4.run_baseline import configure_isolation, prepare_corpus, score_query
from eval.r4.context_experiments import context_metrics, pack_context, renderer_adapter

MODEL = 'qwen3.7-text-embedding-flash'
DIMENSION = 1024
METRIC = 'cosine'
POOL = 5
BUDGET = 700
RRF_CONSTANT = 60


def read(name):
    return json.loads((DIRECTORY/name).read_text(encoding='utf-8'))


def write(name, value):
    # Only files created by this run may be updated; entrypoints reject prior artifacts.
    (DIRECTORY/name).write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8',newline='\n')


def frozen_inputs():
    _, documents, all_cases = load_gold()
    verify_files(read('gold_v1_checksums.json')['files_sha256'])
    baseline = read('baseline_gold_v1_keyword.json')
    cases = [c for c in all_cases if c['split']=='development']
    if len(cases)!=30 or len(baseline['chunk_catalog'])!=79:
        raise ValueError('Frozen corpus size changed')
    return documents, cases, baseline['chunk_catalog']


def validate_vectors(vectors, count, dimension=DIMENSION):
    if len(vectors)!=count:
        raise ValueError('Embedding count mismatch')
    for vector in vectors:
        if (len(vector)!=dimension or any(isinstance(v,bool) or not isinstance(v,(int,float))
                or not math.isfinite(v) for v in vector) or sum(v*v for v in vector)<=0):
            raise ValueError('Invalid embedding dimension, values or zero norm')


def decode_wire_embedding(value):
    """OpenAI SDK defaults to base64 float32 on the wire and decodes after hooks."""
    if isinstance(value,str):
        raw=base64.b64decode(value,validate=True)
        if not raw or len(raw)%4:
            raise ValueError('Invalid base64 float32 embedding')
        vector=array.array('f');vector.frombytes(raw)
        return vector.tolist()
    if isinstance(value,list):
        return value
    raise ValueError('Unknown embedding wire format')


def validate_cache(manifest, cache, catalog, cases):
    if manifest['status']!='complete' or cache['status']!='complete':
        raise ValueError('Incomplete real embedding cache')
    verify_files({'eval/r4/embedding_cache.json':manifest['cache_sha256']})
    verify_files(manifest['source_sha256'])
    profile=manifest['profile']
    if (profile['provider']!='openai' or profile['model']!=MODEL or profile['embedding_dimension']!=DIMENSION
            or profile['normalize_embeddings'] is not False or profile['distance_metric']!=METRIC):
        raise ValueError('Cache profile mismatch')
    for key, record in cache['inputs'].items():
        if key!=sha256(record['text']) or record['input_sha256']!=key:
            raise ValueError('Input digest mismatch')
        validate_vectors([record['embedding']],1)
    expected_chunks=[dict(chunk_key=c['chunk_key'], document_key=c['document_key'], source=c['source'],
        baseline_temp_document_id=c['document_id'], baseline_temp_db_chunk_id=c['db_chunk_id'],
        chunk_index=c['chunk_id'], version_number=c['version_number'], input_sha256=sha256(c['text'])) for c in catalog]
    expected_queries=[dict(id=c['id'],question=c['question'],split='development',input_sha256=sha256(c['question'])) for c in cases]
    if manifest['chunk_bindings']!=expected_chunks or manifest['query_bindings']!=expected_queries:
        raise ValueError('Gold/version/Chunk mapping mismatch')
    expected={sha256(c['text']):c['text'] for c in catalog}
    expected.update({sha256(c['question']):c['question'] for c in cases})
    if set(cache['inputs'])!=set(expected) or any(cache['inputs'][key]['text']!=text for key,text in expected.items()):
        raise ValueError('Input set mismatch')


def validate_partial_cache(manifest,cache,catalog,cases):
    """Never regenerate an existing entry; reject unknown or changed inputs first."""
    verify_files(manifest['source_sha256'])
    verify_files({'eval/r4/embedding_cache.json':manifest['cache_sha256']})
    expected={sha256(c['text']):c['text'] for c in catalog}
    expected.update({sha256(c['question']):c['question'] for c in cases})
    if not set(cache['inputs']).issubset(expected):
        raise ValueError('Unknown cached inputs')
    for key,record in cache['inputs'].items():
        if key!=record['input_sha256'] or record['text']!=expected[key] or sha256(record['text'])!=key:
            raise ValueError('Changed cached text')
        validate_vectors([record['embedding']],1)
    for c,b in zip(catalog,manifest['chunk_bindings']):
        if (c['chunk_key']!=b['chunk_key'] or c['document_key']!=b['document_key'] or
            c['document_id']!=b['baseline_temp_document_id'] or c['db_chunk_id']!=b['baseline_temp_db_chunk_id']
            or c['version_number']!=b['version_number'] or sha256(c['text'])!=b['input_sha256']):
            raise ValueError('Changed Chunk/version mapping')
    if len(manifest['chunk_bindings'])!=len(catalog) or manifest['query_bindings']!=[
            dict(id=c['id'],question=c['question'],split='development',input_sha256=sha256(c['question'])) for c in cases]:
        raise ValueError('Changed input identities')


def resume():
    """Explicitly authorized continuation; skip every validated cached input."""
    _,cases,catalog=frozen_inputs();manifest=read('embedding_cache_manifest.json');cache=read('embedding_cache.json')
    validate_partial_cache(manifest,cache,catalog,cases)
    if manifest['status']=='complete':
        validate_cache(manifest,cache,catalog,cases);print('Cache already complete; no API calls');return
    saved={k:sha256(json.dumps(v,sort_keys=True,separators=(',',':'))) for k,v in cache['inputs'].items()}
    missing_documents=list(dict.fromkeys(c['text'] for c in catalog if sha256(c['text']) not in cache['inputs']))
    missing_queries=list(dict.fromkeys(c['question'] for c in cases if sha256(c['question']) not in cache['inputs']))
    allowed=math.ceil(len(missing_documents)/10)+math.ceil(len(missing_queries)/10)
    run=dict(started_at_utc=datetime.now(timezone.utc).isoformat(),preserved_input_count=len(saved),
        preserved_records_sha256=saved,max_new_http_requests=allowed,new_http_attempts=0,status='preparing')
    manifest.setdefault('continuation_runs',[]).append(run)
    expected_batch=[]
    def persist():
        # Atomic per-file replacement; a mismatched pair is refused rather than silently re-requested.
        for name,value in (('embedding_cache.json',cache),):
            temporary=DIRECTORY/(name+'.tmp')
            temporary.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8',newline='\n')
            temporary.replace(DIRECTORY/name)
        manifest['cache_sha256']=sha256((DIRECTORY/'embedding_cache.json').read_bytes())
        manifest['actual_http_request_attempts']=len(manifest['requests'])
        manifest['validated_input_count']=len(cache['inputs'])
        temporary=DIRECTORY/'embedding_cache_manifest.json.tmp'
        temporary.write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n',encoding='utf-8',newline='\n')
        temporary.replace(DIRECTORY/'embedding_cache_manifest.json')
    try:
        with tempfile.TemporaryDirectory(prefix='r4-embedding-resume-') as folder:
            import os
            os.environ['DATABASE_URL']='sqlite:///'+(Path(folder)/'unused.sqlite').as_posix()
            os.environ['VECTOR_STORE_DIR']=str(Path(folder)/'unused-chroma')
            from backend.rag import embedding_client
            from backend.rag.index_adapters import ExistingEmbeddingAdapter,build_index_target_profile
            from backend.rag.index_contracts import IndexExecutionContext
            import httpx
            target=build_index_target_profile()
            if any(target[k]!=v for k,v in manifest['profile'].items() if k not in ('embedding_dimension','distance_metric')):
                raise ValueError('Embedding Profile changed')
            target.update(manifest['profile'])
            if target['model']!=MODEL or target['provider']!='openai' or target['embedding_dimension']!=DIMENSION:
                raise ValueError('Unexpected Embedding Profile')
            def on_request(request):
                payload=json.loads(request.content)
                if (run['new_http_attempts']>=allowed or request.method!='POST' or not request.url.path.endswith('/embeddings')
                        or payload.get('model')!=MODEL or payload.get('input')!=expected_batch
                        or any(sha256(t) in cache['inputs'] for t in expected_batch)):
                    raise ValueError('Repeated or unauthorized input request')
                run['new_http_attempts']+=1
                manifest['requests'].append(dict(number=len(manifest['requests'])+1,input_count=len(expected_batch),
                    input_sha256=[sha256(t) for t in expected_batch],status='attempted',
                    wire_encoding_format_requested=payload.get('encoding_format')))
                persist()
            def on_response(response):
                log=manifest['requests'][-1];log.update(http_status=response.status_code,status='response_received')
                response.read()
                if response.is_success:
                    data=response.json();log.update(response_model=data.get('model'),usage=data.get('usage'))
                persist()
            run['status']='executing';persist()
            with closing(embedding_client.get_remote_embedding_client()) as base:
                with httpx.Client(event_hooks={'request':[on_request],'response':[on_response]}) as transport:
                    client=base.with_options(http_client=transport,max_retries=0)
                    with patch.object(embedding_client,'get_remote_embedding_client',return_value=client):
                        for group,texts in (('documents',missing_documents),('queries',missing_queries)):
                            texts=[t for t in texts if sha256(t) not in cache['inputs']]
                            for start in range(0,len(texts),10):
                                expected_batch=texts[start:start+10];before=run['new_http_attempts']
                                context=IndexExecutionContext('r4-resume',0,1,'evaluation','evaluation',time.monotonic()+120,time.monotonic)
                                batch=ExistingEmbeddingAdapter().embed(tuple(expected_batch),target_profile=target,context=context)
                                validate_vectors(batch.vectors,len(expected_batch))
                                if run['new_http_attempts']!=before+1 or manifest['requests'][-1].get('response_model')!=MODEL:
                                    raise ValueError('Request count or response model mismatch')
                                if any(batch.profile[k]!=manifest['profile'][k] for k in batch.profile):
                                    raise ValueError('Actual Embedding Profile changed')
                                for text,vector in zip(expected_batch,batch.vectors):
                                    key=sha256(text)
                                    if key in cache['inputs']:raise ValueError('Cannot replace an existing vector')
                                    cache['inputs'][key]=dict(input_sha256=key,text=text,embedding=vector)
                                manifest['requests'][-1].update(status='validated',group=group,actual_dimension=DIMENSION,
                                    decoded_vector_count=len(batch.vectors),decoding='SDK decoded vector objects')
                                persist()
                                print('Saved:',group,'new request',run['new_http_attempts'],'cached inputs',len(cache['inputs']),flush=True)
            if any(sha256(json.dumps(cache['inputs'][k],sort_keys=True,separators=(',',':')))!=v for k,v in saved.items()):
                raise ValueError('Existing vector changed')
            cache['status']='complete';manifest['status']='complete';manifest['unique_input_count']=len(cache['inputs'])
            manifest['dimension_confirmed_by_response']=True;run['status']='complete';persist()
            validate_cache(manifest,cache,catalog,cases)
            print('Complete:',len(cache['inputs']),'real vectors; new requests',run['new_http_attempts'],'lifetime requests',len(manifest['requests']))
    except Exception as exc:
        manifest['status']='failed';cache['status']='partial';run.update(status='failed',failure_type=type(exc).__name__)
        persist();print('STOP:',type(exc).__name__,'validated inputs preserved:',len(cache['inputs']),flush=True)
        raise SystemExit(2) from None


def prepare():
    for name in ('embedding_cache.json','embedding_cache_manifest.json'):
        if (DIRECTORY/name).exists():
            raise ValueError('Existing cache preserved; do not repeat paid requests')
    _,cases,catalog=frozen_inputs()
    with tempfile.TemporaryDirectory(prefix='r4-embed-') as folder:
        # Configure before importing backend settings; dotenv still supplies existing credentials.
        import os
        os.environ['DATABASE_URL']='sqlite:///'+(Path(folder)/'unused.sqlite').as_posix()
        os.environ['VECTOR_STORE_DIR']=str(Path(folder)/'unused-chroma')
        os.environ['HF_HUB_OFFLINE']='1'
        from backend.rag import embedding_client
        from backend.rag.index_adapters import ExistingEmbeddingAdapter, build_index_target_profile
        from backend.rag.index_contracts import IndexExecutionContext
        import httpx
        profile=build_index_target_profile()
        if profile['provider']!='openai' or profile['model']!=MODEL:
            raise ValueError('Current model/provider differs from authorized profile')
        profile.update(embedding_dimension=DIMENSION,distance_metric=METRIC)
        cache=dict(schema_version=1,status='partial',inputs={})
        manifest=dict(schema_version=1,status='preparing',created_at_utc=datetime.now(timezone.utc).isoformat(),
            profile={key:profile[key] for key in ('provider','model','embedding_dimension','normalize_embeddings','endpoint_fingerprint','distance_metric')},
            expected_dimension=DIMENSION,dimension_confirmed_by_response=False,
            collection=dict(name='rag_chunks',configuration={'hnsw':{'space':METRIC}},storage='isolated temporary directory, no business access'),
            chunk_bindings=[dict(chunk_key=c['chunk_key'],document_key=c['document_key'],source=c['source'],
                baseline_temp_document_id=c['document_id'],baseline_temp_db_chunk_id=c['db_chunk_id'],
                chunk_index=c['chunk_id'],version_number=c['version_number'],input_sha256=sha256(c['text'])) for c in catalog],
            query_bindings=[dict(id=c['id'],question=c['question'],split='development',input_sha256=sha256(c['question'])) for c in cases],
            id_scope='Baseline IDs came from disposable evaluation SQLite; stable versioned chunk_key is authoritative, never business IDs.',
            source_sha256={p:sha256((ROOT/p).read_bytes()) for p in ('eval/r4/gold_v1.json','eval/r4/gold_v1_manifest.json',
                'eval/r4/baseline_gold_v1_keyword.json','backend/rag/embedding_client.py','backend/rag/index_adapters.py',
                'backend/rag/chunker.py','backend/rag/vector_store.py')},
            preparation_script_sha256=sha256(Path(__file__).read_bytes()),
            requests=[],sdk_max_retries=0,application_retries=0,batch_size=10,holdout_embedded=False)
        expected_batch=[]
        def request_hook(request):
            payload=json.loads(request.content)
            if (request.method!='POST' or not request.url.path.endswith('/embeddings')
                    or payload.get('model')!=MODEL or payload.get('input')!=expected_batch
                    or len(manifest['requests'])>=11):
                raise ValueError('Unexpected HTTP request; stopped before transmission')
            manifest['requests'].append(dict(number=len(manifest['requests'])+1,input_count=len(expected_batch),
                input_sha256=[sha256(t) for t in expected_batch],status='attempted'))
        def response_hook(response):
            log=manifest['requests'][-1];log['http_status']=response.status_code
            log['status']='response_received'
            response.read()
            if response.is_success:
                data=response.json();returned=data.get('model')
                log['response_model']=returned;log['usage']=data.get('usage')
                vectors=[decode_wire_embedding(item.get('embedding')) for item in data.get('data',[])]
                log['response_dimensions']=sorted({len(vector) for vector in vectors})
                if returned is not None and returned!=MODEL:
                    raise ValueError('Embedding response model mismatch')
                if log['response_dimensions']!=[DIMENSION]:
                    raise ValueError('Embedding response dimension mismatch')
                validate_vectors(vectors,len(expected_batch))
        def persist():
            write('embedding_cache.json',cache)
            manifest['cache_sha256']=sha256((DIRECTORY/'embedding_cache.json').read_bytes())
            manifest['actual_http_request_attempts']=len(manifest['requests'])
            write('embedding_cache_manifest.json',manifest)
        persist()
        try:
            base_client=embedding_client.get_remote_embedding_client()
            with httpx.Client(event_hooks={'request':[request_hook],'response':[response_hook]}) as transport:
                client=base_client.with_options(http_client=transport,max_retries=0)
                with patch.object(embedding_client,'get_remote_embedding_client',return_value=client):
                    for group,texts in (('documents',[c['text'] for c in catalog]),('queries',[c['question'] for c in cases])):
                        unique=list(dict.fromkeys(t for t in texts if sha256(t) not in cache['inputs']))
                        for start in range(0,len(unique),10):
                            expected_batch=unique[start:start+10]
                            before=len(manifest['requests'])
                            context=IndexExecutionContext('r4-real-embedding',0,1,'evaluation','evaluation',time.monotonic()+120,time.monotonic)
                            result=ExistingEmbeddingAdapter().embed(tuple(expected_batch),target_profile=profile,context=context)
                            validate_vectors(result.vectors,len(expected_batch))
                            if len(manifest['requests'])!=before+1:
                                raise ValueError('Unexpected implicit retry/request count')
                            if any(result.profile[key]!=manifest['profile'][key] for key in result.profile):
                                raise ValueError('Embedding profile changed')
                            manifest['dimension_confirmed_by_response']=True
                            manifest['requests'][-1].update(group=group,status='validated',actual_dimension=DIMENSION)
                            for text,vector in zip(expected_batch,result.vectors):
                                key=sha256(text);cache['inputs'][key]=dict(input_sha256=key,text=text,embedding=vector)
                            persist()
                            print('Validated batch',len(manifest['requests']),group,len(expected_batch),'dimension',DIMENSION,flush=True)
            base_client.close()
            cache['status']='complete';manifest['status']='complete'
            manifest['unique_input_count']=len(cache['inputs']);persist()
            validate_cache(manifest,cache,catalog,cases)
        except Exception as exc:
            cache['status']='partial';manifest['status']='failed'
            manifest['failure']=dict(type=type(exc).__name__,http_status=getattr(exc,'status_code',None),
                code=getattr(exc,'code',None) if getattr(exc,'code',None) in ('Arrearage','AllocationQuota.FreeTierOnly','invalid_api_key') else None)
            persist()
            print('STOP: real embedding failed:',manifest['failure'],flush=True)
            raise SystemExit(2) from None


def fuse_rrf(keyword, vector, constant=RRF_CONSTANT, limit=POOL):
    if constant<=0:
        raise ValueError('RRF constant must be positive')
    records={}
    for leg,rows in (('keyword',keyword),('vector',vector)):
        seen=set()
        for row in rows:
            key=row['chunk_key']
            if key in seen: continue
            seen.add(key);rank=len(seen)
            item=records.setdefault(key,dict(row,keyword_rank=None,vector_rank=None,rrf_score=0.0))
            item[leg+'_rank']=rank;item['rrf_score']+=1/(constant+rank)
    ranked=sorted(records.values(),key=lambda r:(-r['rrf_score'],min(r['keyword_rank'] or 10**6,r['vector_rank'] or 10**6),r['chunk_key']))
    return [dict(row,rank=i,score=round(row['rrf_score'],4)) for i,row in enumerate(ranked[:limit],1)]


def summarize(rows):
    summary={}
    for k in (1,3,5):
        group=[r for r in rows if r['top_k']==k]
        relevant=[r for r in group if r['task']=='answerable'];multi=[r for r in relevant if r['multi_evidence_complete'] is not None]
        summary[str(k)]=dict(retrieval_metrics=macro_average([r['retrieval']['scores'] for r in relevant]),
            context_metrics=macro_average([r['scores'] for r in relevant]),
            primary_complete_rate=sum(r['primary_complete'] for r in relevant)/len(relevant),
            multi_complete_rate=sum(r['multi_evidence_complete'] for r in multi)/len(multi),
            average_context_characters=sum(r['context']['characters'] for r in relevant)/len(relevant),
            incomplete_primary_cases=[r['id'] for r in relevant if not r['primary_complete']],
            negative_observations=[dict(id=r['id'],candidate_count=r['candidate_count'],
                visible_count=len(r['context']['rows'])) for r in group if r['task']=='no_positive_evidence'])
    return summary


def evaluation_output_dir(directory):
    directory=Path(directory).resolve()
    if not any(directory.is_relative_to(base.resolve()) for base in (DIRECTORY,ROOT/'eval/local')):
        raise ValueError('Evaluation output must be under eval/r4 or eval/local')
    for name in ('vector_baseline_gold_v1.json','hybrid_rrf_gold_v1.json'):
        if (directory/name).exists(): raise ValueError('Historical output preserved; choose a new --output-dir')
    return directory


def evaluate(output_dir=DIRECTORY):
    output_dir=evaluation_output_dir(output_dir)
    documents,cases,baseline_catalog=frozen_inputs();manifest=read('embedding_cache_manifest.json');cache=read('embedding_cache.json')
    validate_cache(manifest,cache,baseline_catalog,cases)
    with tempfile.TemporaryDirectory(prefix='r4-hybrid-',ignore_cleanup_errors=True) as folder:
        # Load the existing profile before disabling dotenv; storage paths are then overridden by isolation.
        import dotenv
        dotenv.load_dotenv(ROOT/'.env')
        counts=configure_isolation(Path(folder).resolve())
        # Keep its network/SQLite audit hooks; real isolated Chroma is allowed here.
        counts['blocked_embedding_attempts']=counts.pop('model_or_chroma_attempts')
        def offline_guard(frame,event,_arg):
            if event=='call' and frame.f_code.co_filename.replace('\\','/').endswith('backend/rag/embedding_client.py') and frame.f_code.co_name in (
                    'generate_embedding','generate_embeddings','generate_remote_embeddings','generate_local_embeddings','get_local_embedding_model'):
                counts['blocked_embedding_attempts']+=1
                raise RuntimeError('Evaluation must use the precomputed real cache')
        sys.setprofile(offline_guard)
        try:
            import chromadb
            from backend.config import settings
            from backend.rag import vector_store
            from backend.rag.index_adapters import ChromaVectorStoreAdapter,build_index_target_profile
            from backend.rag.index_contracts import IndexExecutionContext
            from backend.rag.retriever import retrieve_top_chunks
            from backend.rag.service import build_rag_context_from_chunks
            chunks,by_id=prepare_corpus(documents)
            if [c['chunk_key'] for c in chunks]!=[c['chunk_key'] for c in baseline_catalog]:
                raise ValueError('Reconstructed Chunk mapping mismatch')
            lookup={c['db_chunk_id']:dict(c,heading_context=[]) for c in chunks}
            path=Path(settings.vector_store_dir).resolve()
            if not path.is_relative_to(Path(folder).resolve()): raise ValueError('Unsafe collection path')
            client=chromadb.PersistentClient(path=str(path),settings=chromadb.Settings(anonymized_telemetry=False))
            collection=client.create_collection('rag_chunks',embedding_function=None,configuration={'hnsw':{'space':METRIC}})
            target=build_index_target_profile()
            for key,value in manifest['profile'].items():
                if key not in ('embedding_dimension','distance_metric') and target[key]!=value:
                    raise ValueError('Current profile differs from embedding cache')
            target.update(manifest['profile'])
            # Collection models are snapshots: refresh after prior upserts set the dimension.
            adapter=ChromaVectorStoreAdapter(collection_factory=lambda:client.get_collection('rag_chunks',embedding_function=None))
            for document_id in by_id:
                subset=tuple(c for c in chunks if c['document_id']==document_id)
                context=IndexExecutionContext('r4-vector-write',document_id,1,'evaluation','knowledge_base:r4_offline',time.monotonic()+120,time.monotonic)
                adapter.upsert(chunks=subset,embeddings=tuple(tuple(cache['inputs'][sha256(c['text'])]['embedding']) for c in subset),target_profile=target,context=context)
            collection=client.get_collection('rag_chunks',embedding_function=None)
            stored=collection.get(include=['documents','metadatas','embeddings'])
            if collection.count()!=79: raise ValueError('Vector count mismatch')
            for id,text,metadata,embedding in zip(stored['ids'],stored['documents'],stored['metadatas'],stored['embeddings']):
                row=lookup[metadata['db_chunk_id']]
                if id!=vector_store.build_vector_id('knowledge_base:r4_offline',row['db_chunk_id']) or text!=row['text'] or metadata['document_id']!=row['document_id']:
                    raise ValueError('Stored vector mapping mismatch')
                validate_vectors([list(embedding)],1)
            class RecordingCollection:
                last=None
                def query(self,**kwargs):
                    self.last=collection.query(**kwargs);return self.last
            recording=RecordingCollection();render=renderer_adapter(build_rag_context_from_chunks)
            pipelines={name:dict(rankings={},results=[]) for name in ('keyword','vector','hybrid_rrf')}
            with patch.object(vector_store,'generate_embedding',side_effect=lambda q:cache['inputs'][sha256(q)]['embedding']),patch.object(vector_store,'get_chroma_collection',return_value=recording),patch.object(vector_store,'settings',replace(settings,rag_vector_score_threshold=0.0)):
                for case in cases:
                    keyword=retrieve_top_chunks(case['question'].strip(),list(lookup.values()),top_k=POOL)
                    retrieved=vector_store.retrieve_similar_chunks(None,case['question'],None,'r4_offline',top_k=POOL)
                    distances={m['db_chunk_id']:float(d) for m,d in zip(recording.last['metadatas'][0],recording.last['distances'][0])}
                    vector=[dict(lookup[row['db_chunk_id']],score=row['score'],distance=distances[row['db_chunk_id']]) for row in retrieved]
                    fused=fuse_rrf(keyword,vector)
                    for name,candidates in (('keyword',keyword),('vector',vector),('hybrid_rrf',fused)):
                        pipelines[name]['rankings'][case['id']]=candidates
                        for k in (1,3,5):
                            context=pack_context(candidates,k,BUDGET,render)
                            pipelines[name]['results'].append(dict(id=case['id'],question=case['question'],task=case['task'],
                                top_k=k,candidate_count=len(candidates),context=context,
                                retrieval=score_query(case,chunks,candidates[:k],k),
                                **context_metrics(case,chunks,context['rows'],k)))
            for pipeline in pipelines.values(): pipeline['summary']=summarize(pipeline['results'])
            info=dict(collection_name=collection.name,distance_metric=METRIC,dimension=collection.get_model().dimension,
                configuration=collection.configuration_json,vector_count=collection.count(),storage_scope='disposable SQLite and Chroma directory',
                isolated_directory=folder,
                vector_score='Existing round(1/(1+distance),4), not probability; threshold disabled (0.0) for this isolated experiment only.')
            client._system.stop()
            del recording,collection,adapter,client
            gc.collect()
        finally:
            sys.setprofile(None)
    verify_files(read('gold_v1_checksums.json')['files_sha256'])
    info['temporary_directory_removed']=not Path(folder).exists()
    metadata=dict(schema_version=1,head=subprocess.check_output(['git','-c','safe.directory='+ROOT.as_posix(),'rev-parse','HEAD'],text=True).strip(),
        split='development',holdout_evaluated=False,profile=manifest['profile'],storage=info,query_rewrite=False,
        category_filter=None,candidate_count_per_leg=POOL,final_candidate_count=POOL,context_budget=BUDGET,
        cache_manifest_sha256=sha256((DIRECTORY/'embedding_cache_manifest.json').read_bytes()),
        source_sha256=manifest['source_sha256'],safety=dict(counts,real_embedding_calls_during_evaluation=0),
        evaluation_script_sha256=sha256(Path(__file__).read_bytes()),
        chunk_catalog=list(lookup.values()),metric_policy='Separate unbudgeted retrieval metrics from visible-body context metrics at K=1/3/5. Same frozen qrels, 700 total context characters, rank-preserving boundary truncation.')
    output_dir.mkdir(parents=True,exist_ok=True)
    def report(name,value):
        with (output_dir/name).open('x',encoding='utf-8',newline='\n') as stream:
            json.dump(value,stream,ensure_ascii=False,indent=2);stream.write('\n')
    report('vector_baseline_gold_v1.json',dict(metadata,pipelines={n:pipelines[n] for n in ('keyword','vector')}))
    report('hybrid_rrf_gold_v1.json',dict(metadata,rrf=dict(constant=RRF_CONSTANT,weights={'keyword':1,'vector':1},
        tie_break='minimum source rank, then versioned chunk_key',tuned=False),pipeline=pipelines['hybrid_rrf'],
        vector_report_sha256=sha256((output_dir/'vector_baseline_gold_v1.json').read_bytes())))
    print('Evaluation complete; cached embeddings only; isolated collection:',info['vector_count'],METRIC)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    commands=parser.add_subparsers(dest='command',required=True)
    commands.add_parser('prepare');commands.add_parser('resume')
    evaluation=commands.add_parser('evaluate')
    evaluation.add_argument('--output-dir',type=Path,default=DIRECTORY)
    args=parser.parse_args()
    if args.command=='evaluate':evaluate(args.output_dir)
    else:{'prepare':prepare,'resume':resume}[args.command]()
