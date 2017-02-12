# coding=utf-8
import json

import tornado.gen

from article_service import ArticleService
from article_type_service import ArticleTypeService
from config import site_cache_keys
from custom_service import BlogInfoService
from extends.utils import AlchemyEncoder, Dict
from menu_service import MenuService
from model.models import BlogView, Comment
from model.site_info import SiteCollection
from plugin_service import PluginService

"""
初始化相关，包括缓存管理
"""


class SiteCacheService(object):
    """SiteCache缓存策略
    站点缓存，加快访问速度，尤其是首页显示的相关数据,该类字段做二级缓存，本地缓存-redis缓存
    查询策略:先查本地缓存，未命中查询redis缓存，还未命中查询数据库，并将结果逐级更新
    更新策略:数据写入数据库后，更新redis缓存，并通过发布对应字段的更新消息通知所有节点更新本地缓存
    缓存校准:mater节点，设置定时任务，在访问较少的时间段校准redis缓存,并通知所有节点更新
    """
    PUB_SUB_MSGS = dict(
        blog_info_updated="blog_info_updated",  # blog_info更新消息
        plugins_updated="plugins_updated",  # plugins更新消息
        menus_updated="menus_updated",  # menus更新消息(包括query_article_types_not_under_menu)
        article_count_updated="article_count_updated",  # article_count更新消息
        article_sources_updated="article_sources_updated",  # article_sources更新消息
        source_articles_count_updated="source_articles_count_updated",  # 某source下的source_articles_count更新消息
    )

    @staticmethod
    @tornado.gen.coroutine
    def query_all(cache_manager, thread_do, db):
        yield SiteCacheService.query_blog_info(cache_manager, thread_do, db)
        yield SiteCacheService.query_menus(cache_manager, thread_do, db)
        yield SiteCacheService.query_plugins(cache_manager, thread_do, db)
        yield SiteCacheService.query_blog_view_count(cache_manager, thread_do, db)
        yield SiteCacheService.query_article_count(cache_manager, thread_do, db)
        yield SiteCacheService.query_comment_count(cache_manager, thread_do, db)
        yield SiteCacheService.query_article_sources(cache_manager, thread_do, db)

    @staticmethod
    @tornado.gen.coroutine
    def query_blog_info(cache_manager, thread_do, db):
        SiteCollection.title = yield cache_manager.call("GET", site_cache_keys['title'])
        SiteCollection.signature = yield cache_manager.call("GET", site_cache_keys['signature'])
        SiteCollection.navbar = yield cache_manager.call("GET", site_cache_keys['navbar'])
        if SiteCollection.title is None or SiteCollection.signature is None or SiteCollection.navbar is None:
            blog_info = yield thread_do(BlogInfoService.get_blog_info, db)
            yield SiteCacheService.update_blog_info(cache_manager, blog_info)

    @staticmethod
    @tornado.gen.coroutine
    def query_menus(cache_manager, thread_do, db):
        menus_json = yield cache_manager.call("GET", site_cache_keys['menus'])
        if menus_json:
            menus = json.loads(menus_json, object_hook=Dict);
            SiteCollection.menus = menus
        ats_json = yield cache_manager.call("GET", site_cache_keys['article_types_not_under_menu'])
        if ats_json:
            ats = json.loads(ats_json, object_hook=Dict);
            SiteCollection.article_types_not_under_menu = ats
        if SiteCollection.menus is None or SiteCollection.article_types_not_under_menu is None:
            menus = yield thread_do(MenuService.list_menus, db, show_types=True)
            article_types_not_under_menu = yield thread_do(ArticleTypeService.list_article_types_not_under_menu, db)
            yield SiteCacheService.update_menus(cache_manager, menus, article_types_not_under_menu)

    @staticmethod
    @tornado.gen.coroutine
    def query_plugins(cache_manager, thread_do, db):
        plugins_json = yield cache_manager.call("GET", site_cache_keys['plugins'])
        if plugins_json:
            plugins = json.loads(plugins_json, object_hook=Dict);
            SiteCollection.plugins = plugins
        if SiteCollection.plugins is None:
            plugins = yield thread_do(PluginService.list_plugins, db)
            yield SiteCacheService.update_plugins(cache_manager, plugins)

    @staticmethod
    @tornado.gen.coroutine
    def query_article_count(cache_manager, thread_do, db):
        article_count = yield cache_manager.call("GET", site_cache_keys['article_count'])
        if article_count is not None:
            SiteCollection.article_count = int(article_count)
        if SiteCollection.article_count is None:
            article_count = yield thread_do(ArticleService.get_count, db)
            if article_count is not None:
                yield SiteCacheService.update_article_count(cache_manager, article_count)

    @staticmethod
    @tornado.gen.coroutine
    def query_article_sources(cache_manager, thread_do, db):
        article_sources_json = yield cache_manager.call("GET", site_cache_keys['article_sources'])
        if article_sources_json:
            article_sources = json.loads(article_sources_json, object_hook=Dict);
            SiteCollection.article_sources = article_sources
            yield SiteCacheService.query_source_articles_count(cache_manager)
        if SiteCollection.article_sources is None:
            article_sources = yield thread_do(ArticleService.get_article_sources, db)
            if article_sources is not None:
                yield SiteCacheService.update_article_sources(cache_manager, article_sources)

    # 仅从cache中查询source下的source_articles_count
    @staticmethod
    @tornado.gen.coroutine
    def query_source_articles_count(cache_manager):
        if SiteCollection.article_sources:
            for source in SiteCollection.article_sources:
                count = yield cache_manager.call("GET", site_cache_keys['source_articles_count'].format(source.id))
                source.articles_count = count

    @staticmethod
    @tornado.gen.coroutine
    def query_blog_view_count(cache_manager, thread_do, db):
        blog_view_count = yield cache_manager.call("GET", site_cache_keys['blog_view_count'])
        if blog_view_count is not None:
            SiteCollection.blog_view_count = int(blog_view_count)
        if SiteCollection.blog_view_count is None:
            SiteCollection.blog_view_count = yield thread_do(get_blog_view_count, db)
            if SiteCollection.blog_view_count is not None:
                yield cache_manager.call("SET", site_cache_keys['blog_view_count'], SiteCollection.blog_view_count)

    @staticmethod
    @tornado.gen.coroutine
    def query_comment_count(cache_manager, thread_do, db):
        comment_count = yield cache_manager.call("GET", site_cache_keys['comment_count'])
        if comment_count is not None:
            SiteCollection.comment_count = int(comment_count)
        if SiteCollection.comment_count is None:
            SiteCollection.comment_count = yield thread_do(get_comment_count, db)
            if SiteCollection.comment_count is not None:
                yield cache_manager.call("SET", site_cache_keys['comment_count'], SiteCollection.comment_count)

# 下面是缓存更新

    @staticmethod
    @tornado.gen.coroutine
    def update_by_sub_msg(msgs, cache_manager, thread_do, db):
        if not msgs:
            pass
        msg = msgs[0]
        if msg == SiteCacheService.PUB_SUB_MSGS['blog_info_updated']:
            yield SiteCacheService.query_blog_info(cache_manager, thread_do, db)
        elif msg == SiteCacheService.PUB_SUB_MSGS['plugins_updated']:
            yield SiteCacheService.query_plugins(cache_manager, thread_do, db)
        elif msg == SiteCacheService.PUB_SUB_MSGS['menus_updated']:
            yield SiteCacheService.query_menus(cache_manager, thread_do, db)
        elif msg == SiteCacheService.PUB_SUB_MSGS['article_count_updated']:
            yield SiteCacheService.query_article_count(cache_manager, thread_do, db)
        elif msg == SiteCacheService.PUB_SUB_MSGS['article_sources_updated']:
            yield SiteCacheService.query_article_sources(cache_manager, thread_do, db)
        elif msg == SiteCacheService.PUB_SUB_MSGS['source_articles_count_updated']:
            yield SiteCacheService.query_source_articles_count(cache_manager)

    @staticmethod
    @tornado.gen.coroutine
    def update_blog_info(cache_manager, blog_info, is_pub_all=False, pubsub_manager=None):
        SiteCollection.title = blog_info.title
        SiteCollection.signature = blog_info.signature
        SiteCollection.navbar = blog_info.navbar
        yield cache_manager.call("SET", site_cache_keys['title'], blog_info.title)
        yield cache_manager.call("SET", site_cache_keys['signature'], blog_info.signature)
        yield cache_manager.call("SET", site_cache_keys['navbar'], blog_info.navbar)
        if is_pub_all:
            yield pubsub_manager.pub_call(SiteCacheService.PUB_SUB_MSGS['blog_info_updated'])

    @staticmethod
    @tornado.gen.coroutine
    def update_plugins(cache_manager, plugins, is_pub_all=False, pubsub_manager=None):
        if plugins is not None:
            SiteCollection.plugins = plugins
            plugins_json = json.dumps(plugins, cls=AlchemyEncoder)
            yield cache_manager.call("SET", site_cache_keys['plugins'], plugins_json)
            if is_pub_all:
                yield pubsub_manager.pub_call(SiteCacheService.PUB_SUB_MSGS['plugins_updated'])

    @staticmethod
    @tornado.gen.coroutine
    def update_menus(cache_manager, menus, article_types_not_under_menu, is_pub_all=False, pubsub_manager=None):
        if menus is not None:
            SiteCollection.menus = menus
            menus_json = json.dumps(menus, cls=AlchemyEncoder)
            yield cache_manager.call("SET", site_cache_keys['menus'], menus_json)
        if article_types_not_under_menu is not None:
            SiteCollection.article_types_not_under_menu = article_types_not_under_menu
            ats_json = json.dumps(article_types_not_under_menu, cls=AlchemyEncoder)
            yield cache_manager.call("SET", site_cache_keys['article_types_not_under_menu'], ats_json)
        if is_pub_all:
            yield pubsub_manager.pub_call(SiteCacheService.PUB_SUB_MSGS['menus_updated'])

    @staticmethod
    @tornado.gen.coroutine
    def update_article_count(cache_manager, article_count, is_pub_all=False, pubsub_manager=None):
        if article_count:
            SiteCollection.article_count = article_count
            yield cache_manager.call("SET", site_cache_keys['article_count'], article_count)
            if is_pub_all:
                yield pubsub_manager.pub_call(SiteCacheService.PUB_SUB_MSGS['article_count_updated'])

    @staticmethod
    @tornado.gen.coroutine
    def update_article_sources(cache_manager, article_sources, is_pub_all=False, pubsub_manager=None):
        if article_sources is not None:
            SiteCollection.article_sources = article_sources
            article_sources_json = json.dumps(article_sources, cls=AlchemyEncoder)
            yield cache_manager.call("SET", site_cache_keys['article_sources'], article_sources_json)
            #  记录对应source下的article_count
            for source in SiteCollection.article_sources:
                yield cache_manager.call("SET", site_cache_keys['source_articles_count'].format(source.id),
                                         source.articles_count)
            if is_pub_all:
                yield pubsub_manager.pub_call(SiteCacheService.PUB_SUB_MSGS['article_sources_updated'])

    # article增删后的操作article_count以及对应的source_count
    @staticmethod
    @tornado.gen.coroutine
    def update_article_action(cache_manager, action, article, is_pub_all=False, pubsub_manager=None):
        if action == "add":
            article_count = yield cache_manager.call("INCR", site_cache_keys['article_count'])
            if article_count:
                SiteCollection.article_count = article_count
                if is_pub_all:
                    yield pubsub_manager.pub_call(SiteCacheService.PUB_SUB_MSGS['article_count_updated'])
            #  注意: 上面的article_count在并发环境下是可以保证安全的，
            #  如果用GET SET会比较难实现。具体该并发问题可以参考：http://www.cnblogs.com/iforever/p/5796902.html
            article_source_id = int(article.source_id)
            source_article_count = \
                yield cache_manager.call("INCR", site_cache_keys['source_articles_count'].format(article_source_id))
            for article_source in SiteCollection.article_sources:
                if int(article_source.id) == article_source_id:
                    article_source.articles_count = source_article_count
                    break
            if is_pub_all:
                yield pubsub_manager.pub_call(SiteCacheService.PUB_SUB_MSGS['source_articles_count_updated'])
        if action == "remove":
            article_count = yield cache_manager.call("DECR", site_cache_keys['article_count'])
            if article_count:
                SiteCollection.article_count = article_count
                if is_pub_all:
                    yield pubsub_manager.pub_call(SiteCacheService.PUB_SUB_MSGS['article_count_updated'])
                #  注意: 上面的article_count在并发环境下是可以保证安全的，
                #  如果用GET SET会比较难实现。具体该并发问题可以参考：http://www.cnblogs.com/iforever/p/5796902.html
                article_source_id = int(article.source_id)
                source_article_count = \
                    yield cache_manager.call("DECR",
                                             site_cache_keys['source_articles_count'].format(article_source_id))
                for article_source in SiteCollection.article_sources:
                    if int(article_source.id) == article_source_id:
                        article_source.articles_count = source_article_count
                        break
                if is_pub_all:
                    yield pubsub_manager.pub_call(SiteCacheService.PUB_SUB_MSGS['source_articles_count_updated'])
        if action == "update":
            article_new = article[0]
            article_old = article[1]
            source_id_old = int(article_old.source_id)
            source_id_new = int(article_new.source_id)
            if source_id_old != source_id_new:
                source_old_article_count = \
                    yield cache_manager.call("DECR",site_cache_keys['source_articles_count'].format(source_id_old))
                source_new_article_count = \
                    yield cache_manager.call("INCR",site_cache_keys['source_articles_count'].format(source_id_new))
                for article_source in SiteCollection.article_sources:
                    if int(article_source.id) == source_id_old:
                        article_source.articles_count = source_old_article_count
                    if int(article_source.id) == source_id_new:
                        article_source.articles_count = source_new_article_count
                if is_pub_all:
                    yield pubsub_manager.pub_call(SiteCacheService.PUB_SUB_MSGS['source_articles_count_updated'])


def get_blog_view_count(db_session):
    blog_view_count = db_session.query(BlogView).first().num_of_view
    return blog_view_count


def get_comment_count(db_session):
    comment_count = db_session.query(Comment).count()
    return comment_count

