// 素材の縦横比(幅 / 高さ)を読むフック。sprite / video の cover と imageSlam の fitStart が使う。
//
// 読み終えるまで delayRender で描画を止める(読めないうちに撮ったコマだけ大きさが違う、を防ぐ)。
// 待ちを解くのは、読んだ値で描き直したあと(次の effect)。読めなかったときは null を返して
// 描画を進める(呼び出し側は箱の大きさで代わりにする)。
// url が null なら何も読まない(使わないとき・色面のとき)。

import React from 'react';
import { continueRender, delayRender } from 'remotion';
import { getVideoMetadata } from '@remotion/media-utils';

/** 読んだ縦横比(同じ素材を何度も読まない)。null は「読めなかった」。 */
const cache = new Map<string, number | null>();

const readAspect = (url: string, kind: 'image' | 'video'): Promise<number | null> => {
  if (kind === 'video') {
    return getVideoMetadata(url).then(
      (meta) => (meta.width > 0 && meta.height > 0 ? meta.width / meta.height : null),
      () => null,
    );
  }
  return new Promise((resolve) => {
    const img = new Image();
    img.onload = () =>
      resolve(
        img.naturalWidth > 0 && img.naturalHeight > 0 ? img.naturalWidth / img.naturalHeight : null,
      );
    img.onerror = () => resolve(null);
    img.src = url;
  });
};

export const useMediaAspect = (url: string | null, kind: 'image' | 'video'): number | null => {
  const key = url === null ? null : `${kind}:${url}`;
  // 読み終えた key(cache に入った物)。描き直しのきっかけに使う。
  const [loaded, setLoaded] = React.useState<string | null>(null);
  // 今かけている待ち(key と delayRender の id)。解いたら null にする。
  const pending = React.useRef<{ key: string; id: number } | null>(null);
  const release = React.useCallback(() => {
    if (pending.current !== null) {
      continueRender(pending.current.id);
      pending.current = null;
    }
  }, []);

  React.useLayoutEffect(() => {
    if (key === null || url === null || cache.has(key)) {
      return;
    }
    pending.current = { key, id: delayRender(`素材の縦横比を読む: ${url}`) };
    let alive = true;
    readAspect(url, kind).then((value) => {
      cache.set(key, value);
      if (value === null) {
        console.warn(`FxOverlay: 素材の縦横比を読めませんでした(箱の大きさで代わりにします): ${url}`);
      }
      if (alive) {
        setLoaded(key);
      }
    });
    return () => {
      // 読み終える前に外された・素材が変わったときも、待ちを残さない。
      alive = false;
      release();
    };
  }, [key, url, kind, release]);

  // 読んだ値で描き直したあとで待ちを解く。
  React.useEffect(() => {
    if (pending.current !== null && loaded === pending.current.key) {
      release();
    }
  }, [loaded, release]);

  return key !== null && cache.has(key) ? (cache.get(key) ?? null) : null;
};
