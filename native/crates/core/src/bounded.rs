//! Reject collection counts while parsing, before large allocations.
use serde::{
    Deserialize, Deserializer,
    de::{Error, IgnoredAny, SeqAccess, Visitor},
};
use std::{fmt, marker::PhantomData};
pub fn list<'de, D, T, const LIMIT: usize>(deserializer: D) -> Result<Vec<T>, D::Error>
where
    D: Deserializer<'de>,
    T: Deserialize<'de>,
{
    struct Bounded<T, const N: usize>(PhantomData<T>);
    impl<'de, T: Deserialize<'de>, const N: usize> Visitor<'de> for Bounded<T, N> {
        type Value = Vec<T>;
        fn expecting(&self, f: &mut fmt::Formatter) -> fmt::Result {
            write!(f, "at most {N} records")
        }
        fn visit_seq<A: SeqAccess<'de>>(self, mut seq: A) -> Result<Vec<T>, A::Error> {
            if seq.size_hint().is_some_and(|n| n > N) {
                return Err(A::Error::custom("记录数量超过安全限制"));
            }
            let mut values = Vec::with_capacity(seq.size_hint().unwrap_or(0).min(N));
            while values.len() < N {
                match seq.next_element()? {
                    Some(value) => values.push(value),
                    None => return Ok(values),
                }
            }
            if seq.next_element::<IgnoredAny>()?.is_some() {
                return Err(A::Error::custom("记录数量超过安全限制"));
            }
            Ok(values)
        }
    }
    deserializer.deserialize_seq(Bounded::<T, LIMIT>(PhantomData))
}
