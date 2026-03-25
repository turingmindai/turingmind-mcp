"""Code indexing tools: index_codebase, get_related_code, get_project_structure."""

from __future__ import annotations

from mcp.types import TextContent

from .context import ToolContext


def register(registry: dict) -> None:
    registry["turingmind_index_codebase"] = handle_index_codebase
    registry["turingmind_get_related_code"] = handle_get_related_code
    registry["turingmind_get_project_structure"] = handle_get_project_structure


async def handle_index_codebase(arguments: dict, ctx: ToolContext) -> list[TextContent]:
    repo = arguments.get("repo", "")
    if not repo:
        return [TextContent(type="text", text="❌ **Missing required field:** `repo`")]
    languages = arguments.get("languages", ["javascript", "typescript", "python"])
    force_reindex = arguments.get("force_reindex", False)
    if not ctx.get_repo_path or not ctx.EntityIndexer or not ctx.get_db:
        return [
            TextContent(
                type="text",
                text="❌ **Code indexing not available:** server missing get_repo_path/EntityIndexer/get_db.",
            )
        ]
    try:
        repo_path = ctx.get_repo_path()
        if not repo_path:
            return [
                TextContent(
                    type="text",
                    text="❌ **Could not determine repository path**\n\nRun this from within a git repository.",
                )
            ]
        indexer = ctx.EntityIndexer(repo_path)
        result = indexer.index_codebase(languages=languages, force_reindex=force_reindex)
        failed_files = result.get("failed_files", [])
        db = ctx.get_db()
        entity_id_map = {}
        entities_stored = 0
        relationships_stored = 0
        with db.transaction() as cursor:
            if force_reindex:
                db.clear_entities_for_repo(repo, _cursor=cursor)
            for entity in result.get("entities", []):
                db_entity_id = db.create_code_entity(
                    repo=repo,
                    file_path=entity["file_path"],
                    entity_type=entity["entity_type"],
                    name=entity["name"],
                    start_line=entity.get("start_line"),
                    end_line=entity.get("end_line"),
                    language=entity.get("language"),
                    _cursor=cursor,
                )
                entities_stored += 1
                key = (entity["file_path"], entity["name"], entity["entity_type"])
                entity_id_map[key] = db_entity_id
                indexer_entity_id = entity.get("entity_id")
                if indexer_entity_id:
                    entity_id_map[indexer_entity_id] = db_entity_id
            relationship_tuples = []
            for rel in result.get("relationships", []):
                source_entity_id_str = rel.get("source_entity_id", "")
                source_id = None
                if source_entity_id_str in entity_id_map:
                    source_id = entity_id_map[source_entity_id_str]
                else:
                    if ":" in source_entity_id_str:
                        parts = source_entity_id_str.split(":")
                        if len(parts) >= 3:
                            file_path, name, entity_type = parts[0], parts[1], ":".join(parts[2:])
                            key = (file_path, name, entity_type)
                            source_id = entity_id_map.get(key)
                target_id = None
                target_entity_id_str = rel.get("target_entity_id")
                if target_entity_id_str and target_entity_id_str in entity_id_map:
                    target_id = entity_id_map[target_entity_id_str]
                if source_id:
                    relationship_tuples.append((
                        repo,
                        source_id,
                        target_id,
                        rel.get("target_symbol_name"),
                        rel.get("relationship_type", "calls"),
                    ))
            if relationship_tuples:
                relationships_stored = db.create_relationship_batch(relationship_tuples, _cursor=cursor)
        response_parts = [
            f"✅ **Codebase Indexed**\n\n",
            f"- **Entities indexed:** {entities_stored}\n",
            f"- **Languages:** {', '.join(languages)}\n",
            f"- **Entity types:** {', '.join(f'{k}: {v}' for k, v in result['entities_by_type'].items())}\n",
            f"- **Relationships:** {relationships_stored}\n",
        ]
        if force_reindex:
            response_parts.append(f"- **Mode:** Full re-index (previous data cleared)\n")
        if failed_files:
            response_parts.append(f"\n⚠️ **{len(failed_files)} files failed to index:**\n")
            for file_path, error in failed_files[:5]:
                response_parts.append(f"  - `{file_path}`: {error}\n")
            if len(failed_files) > 5:
                response_parts.append(f"  - ... and {len(failed_files) - 5} more\n")
        response_parts.append("\nCode entities are now available for relationship-aware reviews.")
        return [TextContent(type="text", text="".join(response_parts))]
    except Exception as e:
        ctx.logger.exception("Indexing failed")
        return [
            TextContent(
                type="text",
                text=(
                    f"❌ **Indexing failed:** {type(e).__name__}: {e}\n\n"
                    f"This may indicate a parsing error or database issue. "
                    f"Try running with `force_reindex: true` to clear and rebuild the index."
                ),
            )
        ]


async def handle_get_related_code(arguments: dict, ctx: ToolContext) -> list[TextContent]:
    repo = arguments.get("repo", "")
    file_path = arguments.get("file", "")
    if not repo or not file_path:
        return [
            TextContent(type="text", text="❌ **Missing required fields:** `repo`, `file`")
        ]
    entity_name = arguments.get("entity_name")
    relationship_types = arguments.get("relationship_types", ["calls", "imports"])
    direction = arguments.get("direction", "both")
    if not ctx.get_db:
        return [TextContent(type="text", text="❌ **Database not available**")]
    try:
        db = ctx.get_db()
        entities = db.get_entities_by_file(repo, file_path)
        if entity_name:
            entity = next((e for e in entities if e["name"] == entity_name), None)
            if not entity:
                return [
                    TextContent(
                        type="text",
                        text=f"❌ **Entity not found:** `{entity_name}` in `{file_path}`",
                    )
                ]
            related = db.get_related_entities(
                entity["entity_id"], relationship_types, direction
            )
        else:
            related = []
            for entity in entities:
                related.extend(
                    db.get_related_entities(
                        entity["entity_id"], relationship_types, direction
                    )
                )
        return [
            TextContent(
                type="text",
                text=(
                    f"🔗 **Related Code Entities**\n\n"
                    f"- **File:** `{file_path}`\n"
                    f"- **Entity:** {entity_name or 'all'}\n"
                    f"- **Related entities:** {len(related)}\n\n"
                    + "\n".join(
                        f"- `{r['file_path']}:{r['name']}` ({r.get('relationship_type', 'unknown')})"
                        for r in related[:20]
                    )
                ),
            )
        ]
    except Exception as e:
        ctx.logger.exception("Get related code failed")
        return [TextContent(type="text", text=f"❌ **Failed:** {type(e).__name__}: {e}")]


async def handle_get_project_structure(arguments: dict, ctx: ToolContext) -> list[TextContent]:
    repo = arguments.get("repo", "")
    if not repo:
        return [TextContent(type="text", text="❌ **Missing required field:** `repo`")]
    if not ctx.get_db:
        return [TextContent(type="text", text="❌ **Database not available**")]
    try:
        db = ctx.get_db()
        with db.transaction() as cursor:
            cursor.execute(
                """
                SELECT entity_type, language, COUNT(*) as count
                FROM code_entities
                WHERE repo = ?
                GROUP BY entity_type, language
                """,
                (repo,),
            )
            stats = cursor.fetchall()
        structure = {}
        for row in stats:
            entity_type, language, count = row[0], row[1] or "unknown", row[2]
            if entity_type not in structure:
                structure[entity_type] = {}
            structure[entity_type][language] = count
        return [
            TextContent(
                type="text",
                text=(
                    f"📊 **Project Structure for {repo}**\n\n"
                    + "\n".join(
                        f"**{et}:**\n"
                        + "\n".join(f"  - {lang}: {count}" for lang, count in langs.items())
                        for et, langs in structure.items()
                    )
                ),
            )
        ]
    except Exception as e:
        ctx.logger.exception("Get project structure failed")
        return [TextContent(type="text", text=f"❌ **Failed:** {type(e).__name__}: {e}")]
