# -*- coding: utf-8 -*-

import datetime
import json
import os
import sys
import argparse
import requests


def abort(msg):
    print('Error!: {0}'.format(msg))
    sys.exit(1)


def print_response(r, title=''):
    c = r.status_code
    h = r.headers
    print('{0} Response={1}, Detail={2}'.format(title, c, h))
    #d = r.json()
    # if 'response_metadata' in d:
    #    print('response_metadata: {0}'.format(d['response_metadata']))


def assert_response(r, title=''):
    c = r.status_code
    h = r.headers
    if title:
        print('Asserting response of {0}...'.format(title))

    if c < 200 or c > 299:
        abort('{0} Response={1}, Detail={2}'.format(title, c, h))

    d = r.json()
    if not d['ok']:
        abort('Not ok from slack, detail:{0}'.format(d['error']))


class Message:
    def __init__(self, d):
        self._ts = d['ts']
        self._type = d['type']

        self._text = ''

        # channels.history で取得したメッセージの text(本文) には
        # 値が入っていないことがある.
        #   例: IFTTT Twitter 連携で流し込まれたメッセージ.
        # その場合はどうしようもないので空値として扱う.
        if ('text' in d) and (d['text'] is not None):
            self._text = d['text'].encode('utf-8')

        self._dtobj = datetime.datetime.fromtimestamp(float(self._ts))
        self._dtstr = self._dtobj.strftime("%Y/%m/%d %H:%M:%S")

    @property
    def ts(self):
        return self._ts

    def __str__(self):
        return """# {0}
{1}

ts:{2}, type:{3}
""".format(self._dtstr, self._text, self._ts, self._type)


class Channel:
    def __init__(self, d):
        self.id = d['id']
        self.name = d['name']

        self.purpose = ''
        try:
            self.purpose = d['purpose']['value'].encode('utf-8')
        except KeyError:
            pass

    def __str__(self):
        return "{0}({1}): {2}".format(self.name, self.id, self.purpose)


class ChannelDictionary:
    def __init__(self, d):
        self.d = {}

        for limited_ch_obj in d:
            ch = Channel(limited_ch_obj)
            key = ch.id
            value = ch
            self.d[key] = value


class SlackAPI:
    def __init__(self):
        self._urlbase = 'https://slack.com/api'
        self._proxies = {
            "http": os.getenv('HTTP_PROXY'),
            "https": os.getenv('HTTPS_PROXY'),
        }
        token = os.getenv('SLACKAPI_TOKEN')
        if not token:
            print("Error: Environment Variable 'SLACKAPI_TOKEN' is None")
            sys.exit(1)
        self._headers = {
            "content-type": "application/json; charset=UTF-8",
            "Authorization": f"Bearer {token}"
        }

    def _post(self, apiname, data):
        return requests.post(f"{self._urlbase}/{apiname}",
                             data=json.dumps(data),
                             headers=self._headers,
                             proxies=self._proxies)

    def _get(self, apiname, data):
        return requests.get(f"{self._urlbase}/{apiname}",
                            data=json.dumps(data),
                            headers=self._headers,
                            proxies=self._proxies)

    def test(self):
        r = self._post("api.test", {})
        assert_response(r, title='Test API')
        resbody = r.json()
        ok = resbody['ok']
        return ok

    def get_users_list(self):
        r = self._get("users.list", {})
        assert_response(r, title='Get all users list')
        resbody = r.json()
        members = resbody['members']
        return members

    def get_channels_list(self):
        r = self._get("conversations.list", {})
        assert_response(r, title='Get all channel list')
        resbody = r.json()
        channels = resbody['channels']
        return channels

    def get_number_of_history(self, chname):
        data = {
            'query': 'in:{0}'.format(chname),
            'count': 1,
        }

        url = self._urlbase + '/search.messages'
        r = self._post(url, data)

        assert_response(
            r, title='Get the number of history of channel "{0}"'.format(chname))
        print_response(r)

        resbody = r.json()
        messages_root = resbody['messages']
        total = messages_root['total']
        return total

    def get_messages(self, channel_id, count=1000, start_ts=None, end_ts=None):
        """ @retrun A Message instances. """
        url = self._urlbase + '/channels.history'
        data = {
            'channel': channel_id,
            'count': count,
            'unreads': 'true',
        }

        # - latest の timestamp を持つメッセージ自体は取得対象にならない.
        # - oldest の timestamp を持つメッセージ自体は取得対象になる.

        if start_ts:
            data['latest'] = float(start_ts)
        if end_ts:
            data['oldest'] = float(end_ts)

        r = self._post(url, data)
        assert_response(r, title='Get Channel History.')
        print_response(r)

        resbody = r.json()
        messages = resbody['messages']
        return messages

    def io_save_messages(self, channel_id, start_ts, end_ts, dstdir):
        # 最初にチャンネル名を取得しておく.
        #
        # チャンネル名の使いみち
        # - 保存先ファイル名に含める.
        # - メッセージ総数取得(検索クエリにチャンネル名が必要).
        chlist = self.get_channels_list()
        chdict = ChannelDictionary(chlist).d
        chname = chdict[channel_id].name

        # メッセージが大量に存在する場合は
        # progress を出さないと精神衛生上よろしくないので
        # 総数を先にゲットしておく.
        total = self.get_number_of_history(chname)
        print('Message total {0} items'.format(total))

        out = ''
        trycount = 1
        totalcount = (total/1000)
        if total % 1000 != 0:
            totalcount += 1
        while True:
            start_ts_text = start_ts
            if start_ts is not None:
                start_ts_text = 'Latest'
            print(
                'Getting {:}/{:} from {:} to next 1000.'
                .format(trycount, totalcount, start_ts_text))

            messages = self.get_messages(channel_id, 1000, start_ts, end_ts)

            messageinst_list = []
            for message in messages:
                msg = Message(message)
                messageinst_list.append(msg)
                out += str(msg) + '\n'

            # 1リクエスト最大件数まで取れてない = もう全部取れた
            if len(messages) < 1000:
                break

            # 次の取得開始位置となる timestamp を取る.
            # Slack API が順番を保証してくれてると信じて tail を見ちゃうよ.
            start_ts = messageinst_list[-1].ts

            trycount += 1

        outpath = os.path.join(dstdir, 'log_{0}.md'.format(chname))
        with open(outpath, 'w') as f:
            f.write(out)


def parse_arguments():
    parser = argparse.ArgumentParser(
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )

    parser.add_argument('--test', default=False,
                        action='store_true', help="Test API")

    list_all_users_help = 'Lists all users in a Slack team'
    parser.add_argument('--list-all-users', default=False,
                        action='store_true', help=list_all_users_help)

    list_all_channels_help = 'Lists all channels in a Slack team'
    parser.add_argument('-l', '--list-all-channels', default=False,
                        action='store_true', help=list_all_channels_help)

    channel_id_help = 'A channel id you want to get all messages from.'
    parser.add_argument('-c', '--channel-id', default='', help=channel_id_help)

    start_help = '[DEBUG] Timestamp. End of time range of messages to include in results.'
    parser.add_argument('--start', default=None, help=start_help)

    end_help = '[DEBUG] Timestamp. Start of time range of messages to include in results.'
    parser.add_argument('--end', default=None, help=end_help)

    parsed_args = parser.parse_args()
    return parsed_args


def main():
    MYDIR = os.path.abspath(os.path.dirname(__file__))
    DATADIR = f"{MYDIR}/data"
    slackapi = SlackAPI()
    args = parse_arguments()

    if args.test:
        print(slackapi.test())
        return

    if args.list_all_users:
        users_list = slackapi.get_users_list()
        with open(f"{DATADIR}/users.json", "w") as fo:
            fo.write(json.dumps(users_list))
        return

    if args.list_all_channels:
        channels_list = slackapi.get_channels_list()
        with open(f"{DATADIR}/channels.json", "w") as fo:
            fo.write(json.dumps(channels_list))
        return
    # print(slackapi.get_users_list())
    # if use_channnelget:
    #     channels = slackapi.get_channel_list()
    #     out = ''
    #     for channel in channels:
    #         ch = Channel(channel)
    #         out += str(ch) + '\n'
    #     print(out)
    #     sys.exit(0)

    # MYDIR = os.path.abspath(os.path.dirname(__file__))
    # slackapi.io_save_messages(channel_id, start_ts, end_ts, MYDIR)


if __name__ == "__main__":
    main()
